from skygear.settings import SettingsParser, add_parser

from .conversation_handlers import (register_conversation_hooks,
                                    register_conversation_lambdas)
from .initialize import register_initialization_event_handlers
from .message_handlers import register_message_hooks, register_message_lambdas
from .receipt_handlers import register_receipt_hooks, register_receipt_lambdas
from .typing import register_typing_lambda
from .user_conversation import register_user_conversation_lambdas


def includeme(settings):
    register_initialization_event_handlers(settings)
    register_conversation_hooks(settings)
    register_conversation_lambdas(settings)
    register_message_hooks(settings)
    register_message_lambdas(settings)
    register_receipt_hooks(settings)
    register_receipt_lambdas(settings)
    register_user_conversation_lambdas(settings)
    register_typing_lambda(settings)


parser = SettingsParser('SKYGEAR_CHAT')

add_parser('chat', parser)
