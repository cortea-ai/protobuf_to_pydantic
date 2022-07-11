import logging
from typing import Any, Dict, List, Optional, Set, Type

from pydantic import validator

from protobuf_to_pydantic.grpc_types import Descriptor, FieldDescriptor, Message, RepeatedCompositeContainer
from protobuf_to_pydantic.util import replace_type

from .customer_validator import validate_validator_dict
from .types import column_pydantic_type_dict

_logger: logging.Logger = logging.getLogger(__name__)
_message_desc_dict: Dict[str, Dict[str, Dict[str, str]]] = {}

type_dict: Dict[str, str] = {
    FieldDescriptor.TYPE_DOUBLE: "double",
    FieldDescriptor.TYPE_FLOAT: "float",
    FieldDescriptor.TYPE_INT64: "int64",
    FieldDescriptor.TYPE_UINT64: "uint64",
    FieldDescriptor.TYPE_INT32: "int32",
    FieldDescriptor.TYPE_FIXED64: "fixed64",
    FieldDescriptor.TYPE_FIXED32: "fixed32",
    FieldDescriptor.TYPE_BOOL: "bool",
    FieldDescriptor.TYPE_STRING: "string",
    FieldDescriptor.TYPE_BYTES: "bytes",
    FieldDescriptor.TYPE_UINT32: "uint32",
    FieldDescriptor.TYPE_ENUM: "enum",
    FieldDescriptor.TYPE_SFIXED32: "sfixed32",
    FieldDescriptor.TYPE_SFIXED64: "sfixed64",
    FieldDescriptor.TYPE_SINT32: "sint32",
    FieldDescriptor.TYPE_SINT64: "sint64",
}

type_not_support_dict: Dict[str, Set[str]] = {
    FieldDescriptor.TYPE_BYTES: {"pattern"},
    FieldDescriptor.TYPE_STRING: {"min_bytes", "max_bytes"},
    "Any": {"ignore_empty", "defined_only"},
}

column_pydantic_dict: Dict[str, str] = {
    "min_len": "min_length",
    "min_bytes": "min_length",
    "max_len": "max_length",
    "max_bytes": "max_length",
    "pattern": "regex",
    "unique": "unique_items",
    "gte": "ge",
    "lte": "le",
    "len_bytes": "len",
    "required": "miss_default",
}


def has_validate(field: Any) -> bool:
    if field.GetOptions() is None:
        return False
    for option_descriptor, option_value in field.GetOptions().ListFields():
        if option_descriptor.full_name == "validate.rules":
            return True
    return False


def _has_field(column: str, message: Message) -> bool:
    if column.startswith("_"):
        return False
    if getattr(Message, column, None):
        return False
    try:
        if not message.HasField(column):
            return False
    except ValueError:
        if not getattr(message, column, None) or column[0].lower() != column[0]:
            return False
    return True


def option_descriptor_to_desc_dict(option_descriptor: Descriptor, field: Any, desc_dict: dict, type_name: str) -> None:

    for column in option_descriptor.__dir__():
        # Removing internal methods
        if not _has_field(column, option_descriptor):  # type: ignore
            continue

        # Exclude unsupported fields
        if column in type_not_support_dict.get(field.type, type_not_support_dict["Any"]):
            _logger.warning(f"{__name__} not support `{column}`, please reset {field.full_name} `{column}` value")
            continue

        value = getattr(option_descriptor, column)
        if column in column_pydantic_dict:
            # Field Conversion
            column = column_pydantic_dict[column]

        if type_name in ("duration", "any", "timestamp") and column in (
            "lt",
            "le",
            "gt",
            "ge",
            "const",
            "in",
            "not_in",
            "lt_now",
            "gt_now",
            "within",
        ):
            # Types of priority treatment for special cases
            if "validator" not in desc_dict:
                desc_dict["validator"] = {}
            desc_dict["extra"][f"{type_name}_{column}"] = replace_type(value)
            desc_dict["validator"][f"{field.name}_{type_name}_{column}_validator"] = validator(
                field.name, allow_reuse=True
            )(validate_validator_dict.get(f"{type_name}_{column}_validator"))
            continue
        elif column in ("in", "not_in", "len", "prefix", "suffix", "contains", "not_contains"):
            # Compatible with PGV attributes that are not supported by pydantic
            if "validator" not in desc_dict:
                desc_dict["validator"] = {}
            print("a ha")
            desc_dict["extra"][column] = replace_type(value)
            desc_dict["validator"][f"{field.name}_{column}_validator"] = validator(field.name, allow_reuse=True)(
                validate_validator_dict.get(f"{column}_validator")
            )
            continue
        elif column in column_pydantic_type_dict:
            desc_dict["type"] = column_pydantic_type_dict[column]
            continue

        # TODO
        # support Repeated items
        # support MapRules
        # support TimestampRules

        desc_dict[column] = value


def get_desc_from_pgv(message: Type[Message]) -> dict:
    if message in _message_desc_dict:
        return _message_desc_dict[message.__name__]

    message_field_dict: dict = {}
    _message_desc_dict[message.__name__] = message_field_dict

    for option_descriptor, option_value in message.DESCRIPTOR.GetOptions().ListFields():
        if (option_descriptor.full_name == "validate.disabled" and option_value) or (
            option_descriptor.full_name == "validate.ignored" and option_value
        ):
            return message_field_dict
    for field in message.DESCRIPTOR.fields:
        type_name: str = type_dict.get(field.type, "")
        if not type_name:
            message_type_name: str = field.message_type.name
            # if message_type_name.endswith("Entry"):
            #     type_name = "map"
            if message_type_name == "Duration":
                type_name = "duration"
            elif message_type_name == "Any":
                type_name = "any"
            elif message_type_name == "Timestamp":
                type_name = "timestamp"
            else:
                _logger.warning(
                    f"{__name__} not support protobuf type id:{field.type} from field name{field.full_name}"
                )
                continue
        field_dict: dict = {"extra": {}}
        miss_default: bool = False
        for option_descriptor, option_value in field.GetOptions().ListFields():
            if option_descriptor.full_name != "validate.rules":
                continue
            rule_message: Any = option_value.message
            if rule_message:
                if getattr(rule_message, "skip", None):
                    _logger.warning(f"{__name__} not support `skip`, please reset {field.full_name} `skip` value")
                if getattr(rule_message, "required", None):
                    miss_default = True
            type_value: Optional[Descriptor] = getattr(option_value, type_name, None)
            if not type_value:
                _logger.warning(f"{__name__}Can not found {field.full_name}'s {type_name} from {option_value}")
                continue
            option_descriptor_to_desc_dict(type_value, field, field_dict, type_name)

        if miss_default:
            field_dict["miss_default"] = miss_default
        message_field_dict[field.name] = field_dict
        _message_desc_dict[message.__name__] = message_field_dict
    return _message_desc_dict
