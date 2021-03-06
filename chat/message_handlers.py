from datetime import datetime

from psycopg2.extensions import AsIs

import skygear
from skygear.models import RecordID, Reference
from skygear.transmitter.encoding import serialize_record
from skygear.utils import db
from skygear.utils.context import current_user_id

from .conversation import Conversation
from .exc import (AlreadyDeletedException, ConversationNotFoundException,
                  MessageNotFoundException, NotInConversationException,
                  NotSupportedException)
from .message import Message
from .message_history import MessageHistory
from .user_conversation import UserConversation
from .utils import _get_schema_name


def get_messages(conversation_id, limit, before_time=None, order=None):
    if not Conversation.exists(conversation_id):
        raise ConversationNotFoundException()
    messages = Message.fetch_all_by_conversation_id(
               conversation_id, limit, before_time, order)
    return {'results': [serialize_record(message) for message in messages]}


def handle_message_before_save(record, original_record, conn):
    message = Message.from_record(record)

    if original_record is not None and original_record['deleted']:
        raise AlreadyDeletedException()

    if UserConversation.fetch_one(message.conversation_id) is None:
        raise NotInConversationException()

    if original_record is None:
        message['deleted'] = False
        message['revision'] = 1
    else:
        message_history = MessageHistory(Message.from_record(original_record))
        message_history.save()
    message['edited_at'] = datetime.utcnow()
    message['edited_by'] = Reference(RecordID('user', current_user_id()))

    if message.get('message_status', None) is None:
        message['message_status'] = 'delivered'

    # TODO use proper ACL setter
    message._acl = Conversation.get_message_acl(message.conversation_id)
    return serialize_record(message)


def handle_message_after_save(record, original_record, conn):
    message = Message.from_record(record)

    event_type = 'create'
    if original_record is not None:
        event_type = 'update'
    if record.get('deleted', False):
        event_type = 'delete'

    message.notifyParticipants(event_type)

    if original_record is None:
        # Update all UserConversation unread count by 1
        conversation_id = message.conversation_id
        conn.execute('''
            UPDATE %(schema_name)s.user_conversation
            SET
                "unread_count" = "unread_count" + 1,
                "_updated_at" = CURRENT_TIMESTAMP
            WHERE
                "conversation" = %(conversation_id)s
                AND "user" != %(user_id)s
        ''', {
            'schema_name': AsIs(_get_schema_name()),
            'conversation_id': conversation_id,
            'user_id': current_user_id()
        })
        conn.execute('''
            UPDATE %(schema_name)s.conversation
            SET "last_message" = %(message_id)s
            WHERE "_id" = %(conversation_id)s
        ''', {
            'schema_name': AsIs(_get_schema_name()),
            'conversation_id': conversation_id,
            'message_id': record.id.key
        })


def _get_new_last_message_id(conn, message):
    # TODO rewrite with database.query
    cur = conn.execute('''
            SELECT _id FROM %(schema_name)s.message
            WHERE deleted = false AND seq < %(seq)s
            ORDER BY seq DESC LIMIT 1
        ''', {
            'schema_name': AsIs(_get_schema_name()),
            'seq': message['seq']
        })
    row = cur.fetchone()
    return None if row is None else row['_id']


def _update_conversation_last_message(conn, conversation, last_message,
                                      new_last_message_id):
    last_message_key = last_message.id.key
    if last_message_key == conversation['last_message_ref'].recordID.key:
        conversation_id = last_message.conversation_id
        conn.execute('''
        UPDATE %(schema_name)s.conversation
        SET last_message = %(new_last_message_id)s
        WHERE _id = %(conversation_id)s
        ''', {
            'schema_name': AsIs(_get_schema_name()),
            'conversation_id': conversation_id,
            'new_last_message_id': 'message/' + new_last_message_id
        })


def _update_user_conversation_last_read_message(conn, last_message,
                                                new_last_message_id):
    conn.execute('''
    UPDATE %(schema_name)s.user_conversation
    SET last_read_message = %(new_last_message_id)s
    WHERE last_read_message = %(old_last_message_id)s
    ''', {
        'schema_name': AsIs(_get_schema_name()),
        'new_last_message_id': new_last_message_id,
        'old_last_message_id': last_message.key
    })


def delete_message(message_id):
    '''
    Delete a message
    - Soft-delete message from record
    - Update last_message and last_read_message
    '''
    message = Message.fetch_one(message_id)
    if message is None:
        raise MessageNotFoundException()

    message.delete()
    record = serialize_record(message)
    conversation = Conversation.fetch_one(message.conversation_id)

    with db.conn() as conn:
        new_last_message_id = _get_new_last_message_id(conn, message)
        _update_conversation_last_message(conn, conversation, message,
                                          new_last_message_id)
        _update_user_conversation_last_read_message(conn, message,
                                                    new_last_message_id)
    return record


def register_message_hooks(settings):
    @skygear.before_save("message", async=False)
    def message_before_save_handler(record, original_record, conn):
        return handle_message_before_save(record, original_record, conn)

    @skygear.after_save("message")
    def message_after_save_handler(record, original_record, conn):
        return handle_message_after_save(record, original_record, conn)

    @skygear.before_delete("message", async=False)
    def message_before_delete_handler(record, conn):
        raise NotSupportedException()


def register_message_lambdas(settings):
    @skygear.op("chat:get_messages", auth_required=True, user_required=True)
    def get_messages_lambda(conversation_id, limit,
                            before_time=None, order=None):
        return get_messages(conversation_id, limit, before_time, order)

    @skygear.op("chat:delete_message", auth_required=True, user_required=True)
    def delete_message_lambda(message_id):
        return delete_message(message_id)
