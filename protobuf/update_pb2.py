# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: update.proto

from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf import reflection as _reflection
from google.protobuf import descriptor_pb2
# @@protoc_insertion_point(imports)




DESCRIPTOR = _descriptor.FileDescriptor(
  name='update.proto',
  package='sdn',
  serialized_pb='\n\x0cupdate.proto\x12\x03sdn\"n\n\x06Update\x12%\n\x05utype\x18\x01 \x02(\x0e\x32\x16.sdn.Update.UpdateType\x12\x0c\n\x04\x64\x61ta\x18\x02 \x02(\t\"/\n\nUpdateType\x12\t\n\x05QUERY\x10\x00\x12\x0c\n\x08RESPONSE\x10\x01\x12\x08\n\x04\x46LOW\x10\x02\"8\n\x06\x42undle\x12\x11\n\ttimestamp\x18\x01 \x02(\x05\x12\x1b\n\x06update\x18\x02 \x03(\x0b\x32\x0b.sdn.Update\"#\n\x08Neighbor\x12\n\n\x02ip\x18\x01 \x02(\t\x12\x0b\n\x03rtt\x18\x02 \x01(\x02\"=\n\x06Report\x12\x11\n\ttimestamp\x18\x01 \x02(\x02\x12 \n\tneighbors\x18\x02 \x03(\x0b\x32\r.sdn.Neighbor')



_UPDATE_UPDATETYPE = _descriptor.EnumDescriptor(
  name='UpdateType',
  full_name='sdn.Update.UpdateType',
  filename=None,
  file=DESCRIPTOR,
  values=[
    _descriptor.EnumValueDescriptor(
      name='QUERY', index=0, number=0,
      options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='RESPONSE', index=1, number=1,
      options=None,
      type=None),
    _descriptor.EnumValueDescriptor(
      name='FLOW', index=2, number=2,
      options=None,
      type=None),
  ],
  containing_type=None,
  options=None,
  serialized_start=84,
  serialized_end=131,
)


_UPDATE = _descriptor.Descriptor(
  name='Update',
  full_name='sdn.Update',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  fields=[
    _descriptor.FieldDescriptor(
      name='utype', full_name='sdn.Update.utype', index=0,
      number=1, type=14, cpp_type=8, label=2,
      has_default_value=False, default_value=0,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
    _descriptor.FieldDescriptor(
      name='data', full_name='sdn.Update.data', index=1,
      number=2, type=9, cpp_type=9, label=2,
      has_default_value=False, default_value=unicode("", "utf-8"),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
    _UPDATE_UPDATETYPE,
  ],
  options=None,
  is_extendable=False,
  extension_ranges=[],
  serialized_start=21,
  serialized_end=131,
)


_BUNDLE = _descriptor.Descriptor(
  name='Bundle',
  full_name='sdn.Bundle',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  fields=[
    _descriptor.FieldDescriptor(
      name='timestamp', full_name='sdn.Bundle.timestamp', index=0,
      number=1, type=5, cpp_type=1, label=2,
      has_default_value=False, default_value=0,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
    _descriptor.FieldDescriptor(
      name='update', full_name='sdn.Bundle.update', index=1,
      number=2, type=11, cpp_type=10, label=3,
      has_default_value=False, default_value=[],
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  options=None,
  is_extendable=False,
  extension_ranges=[],
  serialized_start=133,
  serialized_end=189,
)


_NEIGHBOR = _descriptor.Descriptor(
  name='Neighbor',
  full_name='sdn.Neighbor',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  fields=[
    _descriptor.FieldDescriptor(
      name='ip', full_name='sdn.Neighbor.ip', index=0,
      number=1, type=9, cpp_type=9, label=2,
      has_default_value=False, default_value=unicode("", "utf-8"),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
    _descriptor.FieldDescriptor(
      name='rtt', full_name='sdn.Neighbor.rtt', index=1,
      number=2, type=2, cpp_type=6, label=1,
      has_default_value=False, default_value=0,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  options=None,
  is_extendable=False,
  extension_ranges=[],
  serialized_start=191,
  serialized_end=226,
)


_REPORT = _descriptor.Descriptor(
  name='Report',
  full_name='sdn.Report',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  fields=[
    _descriptor.FieldDescriptor(
      name='timestamp', full_name='sdn.Report.timestamp', index=0,
      number=1, type=2, cpp_type=6, label=2,
      has_default_value=False, default_value=0,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
    _descriptor.FieldDescriptor(
      name='neighbors', full_name='sdn.Report.neighbors', index=1,
      number=2, type=11, cpp_type=10, label=3,
      has_default_value=False, default_value=[],
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  options=None,
  is_extendable=False,
  extension_ranges=[],
  serialized_start=228,
  serialized_end=289,
)

_UPDATE.fields_by_name['utype'].enum_type = _UPDATE_UPDATETYPE
_UPDATE_UPDATETYPE.containing_type = _UPDATE;
_BUNDLE.fields_by_name['update'].message_type = _UPDATE
_REPORT.fields_by_name['neighbors'].message_type = _NEIGHBOR
DESCRIPTOR.message_types_by_name['Update'] = _UPDATE
DESCRIPTOR.message_types_by_name['Bundle'] = _BUNDLE
DESCRIPTOR.message_types_by_name['Neighbor'] = _NEIGHBOR
DESCRIPTOR.message_types_by_name['Report'] = _REPORT

class Update(_message.Message):
  __metaclass__ = _reflection.GeneratedProtocolMessageType
  DESCRIPTOR = _UPDATE

  # @@protoc_insertion_point(class_scope:sdn.Update)

class Bundle(_message.Message):
  __metaclass__ = _reflection.GeneratedProtocolMessageType
  DESCRIPTOR = _BUNDLE

  # @@protoc_insertion_point(class_scope:sdn.Bundle)

class Neighbor(_message.Message):
  __metaclass__ = _reflection.GeneratedProtocolMessageType
  DESCRIPTOR = _NEIGHBOR

  # @@protoc_insertion_point(class_scope:sdn.Neighbor)

class Report(_message.Message):
  __metaclass__ = _reflection.GeneratedProtocolMessageType
  DESCRIPTOR = _REPORT

  # @@protoc_insertion_point(class_scope:sdn.Report)


# @@protoc_insertion_point(module_scope)
