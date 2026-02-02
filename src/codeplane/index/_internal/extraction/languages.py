"""Language-specific query configurations.

Defines extraction queries for all supported languages using tree-sitter query syntax.
Each language has a LanguageQueryConfig with patterns for:
- Type annotations (parameters, variables, returns, fields)
- Type members (methods, fields, properties)
- Member accesses (dot, arrow, scope)
- Interface implementations

Query Capture Naming Convention:
- @name: identifier being annotated/defined
- @type: type annotation
- @param: marks this as a parameter annotation
- @return: marks this as a return type annotation
- @field: marks this as a field annotation
- @parent: parent type name (class/struct/interface)
- @member: member name
- @method: marks this as a method
- @visibility: access modifier
- @static: marks as static
- @receiver: member access receiver
- @implementor: class implementing interface
- @interface: interface being implemented
"""

from codeplane.index._internal.extraction.query_based import LanguageQueryConfig

# =============================================================================
# PYTHON
# =============================================================================

PYTHON_CONFIG = LanguageQueryConfig(
    language_family="python",
    grammar_name="python",
    scope_node_types=["function_definition", "class_definition"],
    member_access_types=["attribute"],
    optional_patterns=["Optional[", "| None", "None |"],
    array_patterns=["list[", "List[", "Sequence[", "tuple[", "Tuple[", "set[", "Set["],
    generic_indicator="[",
    supports_interfaces=False,
    type_annotation_query="""
; Function parameters with type annotations
(typed_parameter
  (identifier) @name
  type: (type) @type) @param

(typed_default_parameter
  name: (identifier) @name
  type: (type) @type) @param

; Function return types
(function_definition
  name: (identifier) @name
  return_type: (type) @type) @return

; Variable annotations
(assignment
  left: (identifier) @name
  type: (type) @type)
""",
    type_member_query="""
; Class methods
(class_definition
  name: (identifier) @parent
  body: (block
    (function_definition
      name: (identifier) @member) @method))

; Class fields (annotated assignments in class body)
(class_definition
  name: (identifier) @parent
  body: (block
    (expression_statement
      (assignment
        left: (identifier) @member
        type: (type) @type))))
""",
    member_access_query="""
(attribute
  object: (identifier) @receiver
  attribute: (identifier) @member) @expr

(call
  function: (attribute
    object: (identifier) @receiver
    attribute: (identifier) @member) @expr) @call
""",
)

# =============================================================================
# TYPESCRIPT / JAVASCRIPT
# =============================================================================

TYPESCRIPT_CONFIG = LanguageQueryConfig(
    language_family="javascript",
    grammar_name="typescript",
    scope_node_types=[
        "function_declaration",
        "method_definition",
        "class_declaration",
        "arrow_function",
    ],
    member_access_types=["member_expression"],
    optional_patterns=["| null", "| undefined", "?"],
    array_patterns=["[]", "Array<", "ReadonlyArray<"],
    generic_indicator="<",
    supports_interfaces=True,
    type_annotation_query="""
; Function parameters
(required_parameter
  pattern: (identifier) @name
  type: (type_annotation (_) @type)) @param

(optional_parameter
  pattern: (identifier) @name
  type: (type_annotation (_) @type)) @param

; Function return types
(function_declaration
  name: (identifier) @name
  return_type: (type_annotation (_) @type)) @return

(method_definition
  name: (property_identifier) @name
  return_type: (type_annotation (_) @type)) @return

(arrow_function
  return_type: (type_annotation (_) @type)) @return

; Variable declarations
(variable_declarator
  name: (identifier) @name
  type: (type_annotation (_) @type))

; Class properties
(public_field_definition
  name: (property_identifier) @name
  type: (type_annotation (_) @type)) @field

; Interface properties
(property_signature
  name: (property_identifier) @name
  type: (type_annotation (_) @type)) @field
""",
    type_member_query="""
; Class methods
(class_declaration
  name: (type_identifier) @parent
  body: (class_body
    (method_definition
      name: (property_identifier) @member) @method))

; Class fields
(class_declaration
  name: (type_identifier) @parent
  body: (class_body
    (public_field_definition
      name: (property_identifier) @member
      type: (type_annotation (_) @type)?)))

; Interface methods
(interface_declaration
  name: (type_identifier) @parent
  body: (interface_body
    (method_signature
      name: (property_identifier) @member) @method))

; Interface properties
(interface_declaration
  name: (type_identifier) @parent
  body: (interface_body
    (property_signature
      name: (property_identifier) @member
      type: (type_annotation (_) @type)?)))
""",
    member_access_query="""
(member_expression
  object: (identifier) @receiver
  property: (property_identifier) @member) @expr

(call_expression
  function: (member_expression
    object: (identifier) @receiver
    property: (property_identifier) @member) @expr) @call
""",
    interface_impl_query="""
(class_declaration
  name: (type_identifier) @implementor
  (class_heritage
    (implements_clause
      (type_identifier) @interface)))
""",
)

# =============================================================================
# GO
# =============================================================================

GO_CONFIG = LanguageQueryConfig(
    language_family="go",
    grammar_name="go",
    scope_node_types=["function_declaration", "method_declaration"],
    member_access_types=["selector_expression"],
    member_identifier_types=["field_identifier"],
    optional_patterns=[],
    array_patterns=["[]"],
    generic_indicator="[",
    reference_indicator="*",
    supports_interfaces=True,
    type_annotation_query="""
; Function parameters
(parameter_declaration
  name: (identifier) @name
  type: (_) @type) @param

; Function return types
(function_declaration
  name: (identifier) @name
  result: (_) @type) @return

(method_declaration
  name: (field_identifier) @name
  result: (_) @type) @return

; Variable declarations
(var_spec
  name: (identifier) @name
  type: (_) @type)

; Short declarations don't have types (inferred)

; Const declarations
(const_spec
  name: (identifier) @name
  type: (_) @type)
""",
    type_member_query="""
; Struct fields
(type_declaration
  (type_spec
    name: (type_identifier) @parent
    type: (struct_type
      (field_declaration_list
        (field_declaration
          name: (field_identifier) @member)))))

; Interface methods
(type_declaration
  (type_spec
    name: (type_identifier) @parent
    type: (interface_type
      (method_elem
        (field_identifier) @member) @method)))
""",
    member_access_query="""
(selector_expression
  operand: (identifier) @receiver
  field: (field_identifier) @member) @expr

(call_expression
  function: (selector_expression
    operand: (identifier) @receiver
    field: (field_identifier) @member) @expr) @call
""",
    # Go uses structural typing - no explicit implements
    interface_impl_query="",
)

# =============================================================================
# RUST
# =============================================================================

RUST_CONFIG = LanguageQueryConfig(
    language_family="rust",
    grammar_name="rust",
    scope_node_types=["function_item", "impl_item"],
    member_access_types=["field_expression"],
    member_identifier_types=["field_identifier"],
    access_styles=["dot", "scope"],
    optional_patterns=["Option<"],
    array_patterns=["Vec<", "["],
    generic_indicator="<",
    reference_indicator="&",
    supports_interfaces=True,
    type_annotation_query="""
; Function parameters
(parameter
  pattern: (identifier) @name
  type: (_) @type) @param

; Function return types
(function_item
  name: (identifier) @name
  return_type: (_) @type) @return

; Let bindings with type
(let_declaration
  pattern: (identifier) @name
  type: (_) @type)

; Const items
(const_item
  name: (identifier) @name
  type: (_) @type)

; Static items
(static_item
  name: (identifier) @name
  type: (_) @type)
""",
    type_member_query="""
; Struct fields
(struct_item
  name: (type_identifier) @parent
  body: (field_declaration_list
    (field_declaration
      name: (field_identifier) @member
      type: (_) @type)))

; Enum variants
(enum_item
  name: (type_identifier) @parent
  body: (enum_variant_list
    (enum_variant
      name: (identifier) @member)))

; Trait methods
(trait_item
  name: (type_identifier) @parent
  body: (declaration_list
    (function_signature_item
      name: (identifier) @member) @method))

; Impl methods
(impl_item
  type: (type_identifier) @parent
  body: (declaration_list
    (function_item
      name: (identifier) @member) @method))
""",
    member_access_query="""
(field_expression
  value: (identifier) @receiver
  field: (field_identifier) @member) @expr

(call_expression
  function: (field_expression
    value: (identifier) @receiver
    field: (field_identifier) @member) @expr) @call

; Scoped paths (Type::method)
(scoped_identifier
  path: (identifier) @receiver
  name: (identifier) @member) @expr @scope
""",
    interface_impl_query="""
(impl_item
  trait: (type_identifier) @interface
  "for"
  type: (type_identifier) @implementor)
""",
)

# =============================================================================
# JAVA
# =============================================================================

JAVA_CONFIG = LanguageQueryConfig(
    language_family="jvm",
    grammar_name="java",
    scope_node_types=["method_declaration", "constructor_declaration", "class_declaration"],
    member_access_types=["field_access", "method_invocation"],
    optional_patterns=["Optional<"],
    array_patterns=["[]", "List<", "Set<", "Collection<"],
    generic_indicator="<",
    supports_interfaces=True,
    type_annotation_query="""
; Method parameters
(formal_parameter
  type: (_) @type
  name: (identifier) @name) @param

; Method return types
(method_declaration
  type: (_) @type
  name: (identifier) @name) @return

; Field declarations
(field_declaration
  type: (_) @type
  declarator: (variable_declarator
    name: (identifier) @name)) @field

; Local variables
(local_variable_declaration
  type: (_) @type
  declarator: (variable_declarator
    name: (identifier) @name))
""",
    type_member_query="""
; Class methods
(class_declaration
  name: (identifier) @parent
  body: (class_body
    (method_declaration
      name: (identifier) @member) @method))

; Class fields
(class_declaration
  name: (identifier) @parent
  body: (class_body
    (field_declaration
      declarator: (variable_declarator
        name: (identifier) @member))))

; Interface methods
(interface_declaration
  name: (identifier) @parent
  body: (interface_body
    (method_declaration
      name: (identifier) @member) @method))

; Enum constants
(enum_declaration
  name: (identifier) @parent
  body: (enum_body
    (enum_constant
      name: (identifier) @member)))
""",
    member_access_query="""
(field_access
  object: (identifier) @receiver
  field: (identifier) @member) @expr

(method_invocation
  object: (identifier) @receiver
  name: (identifier) @member
  arguments: (argument_list) @args) @call
""",
    interface_impl_query="""
(class_declaration
  name: (identifier) @implementor
  interfaces: (super_interfaces
    (type_list
      (_) @interface)))
""",
)

# =============================================================================
# C#
# =============================================================================

CSHARP_CONFIG = LanguageQueryConfig(
    language_family="dotnet",
    grammar_name="c_sharp",
    scope_node_types=["method_declaration", "constructor_declaration", "class_declaration"],
    member_access_types=["member_access_expression"],
    optional_patterns=["?"],
    array_patterns=["[]", "List<", "IEnumerable<", "ICollection<"],
    generic_indicator="<",
    supports_interfaces=True,
    type_annotation_query="""
; Method parameters
(parameter
  type: (_) @type
  name: (identifier) @name) @param

; Method return types
(method_declaration
  returns: (_) @type
  name: (identifier) @name) @return

; Property declarations
(property_declaration
  type: (_) @type
  name: (identifier) @name) @field

; Field declarations
(field_declaration
  (variable_declaration
    type: (_) @type
    (variable_declarator
      (identifier) @name))) @field

; Local variables
(local_declaration_statement
  (variable_declaration
    type: (_) @type
    (variable_declarator
      (identifier) @name)))
""",
    type_member_query="""
; Class methods
(class_declaration
  name: (identifier) @parent
  body: (declaration_list
    (method_declaration
      name: (identifier) @member) @method))

; Class properties
(class_declaration
  name: (identifier) @parent
  body: (declaration_list
    (property_declaration
      name: (identifier) @member)))

; Class fields
(class_declaration
  name: (identifier) @parent
  body: (declaration_list
    (field_declaration
      (variable_declaration
        (variable_declarator
          (identifier) @member)))))

; Interface methods
(interface_declaration
  name: (identifier) @parent
  body: (declaration_list
    (method_declaration
      name: (identifier) @member) @method))
""",
    member_access_query="""
(member_access_expression
  expression: (identifier) @receiver
  name: (identifier) @member) @expr

(invocation_expression
  function: (member_access_expression
    expression: (identifier) @receiver
    name: (identifier) @member) @expr
  arguments: (argument_list) @args) @call
""",
    interface_impl_query="""
(class_declaration
  name: (identifier) @implementor
  (base_list
    (_) @interface))

(struct_declaration
  name: (identifier) @implementor
  (base_list
    (_) @interface))
""",
)

# =============================================================================
# C / C++
# =============================================================================

CPP_CONFIG = LanguageQueryConfig(
    language_family="cpp",
    grammar_name="cpp",
    scope_node_types=["function_definition", "class_specifier", "struct_specifier"],
    member_access_types=["field_expression"],
    member_identifier_types=["field_identifier"],
    access_styles=["dot", "arrow", "scope"],
    optional_patterns=[],
    array_patterns=["[]", "vector<", "array<"],
    generic_indicator="<",
    reference_indicator="&",
    supports_interfaces=True,
    type_annotation_query="""
; Function parameters
(parameter_declaration
  type: (_) @type
  declarator: (identifier) @name) @param

(parameter_declaration
  type: (_) @type
  declarator: (pointer_declarator
    declarator: (identifier) @name)) @param

; Function return types
(function_definition
  type: (_) @type
  declarator: (function_declarator
    declarator: (identifier) @name)) @return

; Variable declarations
(declaration
  type: (_) @type
  declarator: (init_declarator
    declarator: (identifier) @name))
""",
    type_member_query="""
; Struct/class fields
(struct_specifier
  name: (type_identifier) @parent
  body: (field_declaration_list
    (field_declaration
      type: (_) @type
      declarator: (field_identifier) @member)))

(class_specifier
  name: (type_identifier) @parent
  body: (field_declaration_list
    (field_declaration
      type: (_) @type
      declarator: (field_identifier) @member)))

; Class methods (inside class body)
(class_specifier
  name: (type_identifier) @parent
  body: (field_declaration_list
    (function_definition
      declarator: (function_declarator
        declarator: (identifier) @member)) @method))
""",
    member_access_query="""
; Dot access
(field_expression
  argument: (identifier) @receiver
  field: (field_identifier) @member) @expr

; Arrow access (pointers)
(field_expression
  argument: (identifier) @receiver
  "->" @arrow
  field: (field_identifier) @member) @expr

; Scoped access
(qualified_identifier
  scope: (namespace_identifier) @receiver
  name: (identifier) @member) @expr @scope

(call_expression
  function: (field_expression
    argument: (identifier) @receiver
    field: (field_identifier) @member) @expr
  arguments: (argument_list) @args) @call
""",
    interface_impl_query="""
(class_specifier
  name: (type_identifier) @implementor
  (base_class_clause
    (type_identifier) @interface))
""",
)

# =============================================================================
# SWIFT
# =============================================================================

SWIFT_CONFIG = LanguageQueryConfig(
    language_family="swift",
    grammar_name="swift",
    scope_node_types=["function_declaration", "class_declaration"],
    member_access_types=["navigation_expression"],
    optional_patterns=["?"],
    array_patterns=["[", "Array<"],
    generic_indicator="<",
    supports_interfaces=True,
    type_annotation_query="""
; Function parameters
(parameter
  (simple_identifier) @name
  (type_annotation
    (_) @type)) @param

; Function return types
(function_declaration
  name: (simple_identifier) @name
  (function_signature
    (return_clause
      (_) @type))) @return

; Variable declarations
(property_declaration
  (pattern
    (simple_identifier) @name)
  (type_annotation
    (_) @type))

; Let/var with type
(local_declaration
  (value_binding_pattern
    (pattern
      (simple_identifier) @name)
    (type_annotation
      (_) @type)))
""",
    type_member_query="""
; Class methods
(class_declaration
  name: (type_identifier) @parent
  body: (class_body
    (function_declaration
      name: (simple_identifier) @member) @method))

; Class properties
(class_declaration
  name: (type_identifier) @parent
  body: (class_body
    (property_declaration
      (pattern
        (simple_identifier) @member)
      (type_annotation
        (_) @type)?)))

; Protocol methods
(protocol_declaration
  name: (type_identifier) @parent
  body: (protocol_body
    (protocol_method_declaration
      name: (simple_identifier) @member) @method))
""",
    member_access_query="""
(navigation_expression
  target: (simple_identifier) @receiver
  suffix: (navigation_suffix
    (simple_identifier) @member)) @expr

(call_expression
  (navigation_expression
    target: (simple_identifier) @receiver
    suffix: (navigation_suffix
      (simple_identifier) @member)) @expr) @call
""",
    interface_impl_query="""
(class_declaration
  name: (type_identifier) @implementor
  (inheritance_clause
    (inheritance_specifier
      (type_identifier) @interface)))
""",
)

# =============================================================================
# RUBY
# =============================================================================

RUBY_CONFIG = LanguageQueryConfig(
    language_family="ruby",
    grammar_name="ruby",
    scope_node_types=["method", "class"],
    member_access_types=["call"],
    optional_patterns=[],
    array_patterns=["Array"],
    generic_indicator="",  # Ruby doesn't have generics
    supports_type_annotations=False,  # Native Ruby has no types; Sorbet is comment-based
    supports_interfaces=False,
    type_annotation_query="",  # No native type annotations
    type_member_query="""
; Class methods
(class
  name: (constant) @parent
  body: (body_statement
    (method
      name: (identifier) @member) @method)?)

; Singleton class methods (self.method)
(class
  name: (constant) @parent
  body: (body_statement
    (singleton_method
      name: (identifier) @member) @method @static)?)

; attr_accessor/attr_reader/attr_writer
(class
  name: (constant) @parent
  body: (body_statement
    (call
      method: (identifier) @kind
      arguments: (argument_list
        (simple_symbol) @member))))
""",
    member_access_query="""
(call
  receiver: (identifier) @receiver
  method: (identifier) @member) @expr

(call
  receiver: (identifier) @receiver
  method: (identifier) @member
  arguments: (argument_list) @args) @call
""",
)

# =============================================================================
# PHP
# =============================================================================

PHP_CONFIG = LanguageQueryConfig(
    language_family="php",
    grammar_name="php",
    scope_node_types=["method_declaration", "function_definition", "class_declaration"],
    member_access_types=["member_access_expression"],
    access_styles=["arrow"],  # PHP uses ->
    optional_patterns=["?"],
    array_patterns=["array", "iterable"],
    generic_indicator="",  # PHP doesn't have native generics
    supports_interfaces=True,
    type_annotation_query="""
; Function parameters with type hints
(simple_parameter
  type: (_) @type
  name: (variable_name) @name) @param

; Function return types
(function_definition
  name: (name) @name
  return_type: (union_type) @type) @return

(function_definition
  name: (name) @name
  return_type: (named_type) @type) @return

(method_declaration
  name: (name) @name
  return_type: (_) @type) @return

; Property declarations
(property_declaration
  type: (_) @type
  (property_element
    (variable_name) @name)) @field
""",
    type_member_query="""
; Class methods
(class_declaration
  name: (name) @parent
  body: (declaration_list
    (method_declaration
      name: (name) @member) @method))

; Class properties
(class_declaration
  name: (name) @parent
  body: (declaration_list
    (property_declaration
      type: (_) @type?
      (property_element
        (variable_name) @member))))

; Interface methods
(interface_declaration
  name: (name) @parent
  body: (declaration_list
    (method_declaration
      name: (name) @member) @method))
""",
    member_access_query="""
(member_access_expression
  object: (variable_name) @receiver
  name: (name) @member) @expr

(member_call_expression
  object: (variable_name) @receiver
  name: (name) @member
  arguments: (arguments) @args) @call
""",
    interface_impl_query="""
(class_declaration
  name: (name) @implementor
  (class_interface_clause
    (name) @interface))
""",
)

# =============================================================================
# KOTLIN
# =============================================================================

KOTLIN_CONFIG = LanguageQueryConfig(
    language_family="jvm",
    grammar_name="kotlin",
    scope_node_types=["function_declaration", "class_declaration"],
    member_access_types=["navigation_expression"],
    optional_patterns=["?"],
    array_patterns=["List<", "Array<", "Set<", "Collection<"],
    generic_indicator="<",
    supports_interfaces=True,
    type_annotation_query="""
; Function parameters
(parameter
  (simple_identifier) @name
  (user_type) @type) @param

; Function return types
(function_declaration
  (simple_identifier) @name
  (user_type) @type) @return

; Property declarations
(property_declaration
  (variable_declaration
    (simple_identifier) @name
    (user_type) @type?))
""",
    type_member_query="""
; Class functions
(class_declaration
  (type_identifier) @parent
  (class_body
    (function_declaration
      (simple_identifier) @member) @method))

; Class properties
(class_declaration
  (type_identifier) @parent
  (class_body
    (property_declaration
      (variable_declaration
        (simple_identifier) @member))))

; Interface functions
(interface_declaration
  (type_identifier) @parent
  (interface_body
    (function_declaration
      (simple_identifier) @member) @method))
""",
    member_access_query="""
(navigation_expression
  (simple_identifier) @receiver
  (navigation_suffix
    (simple_identifier) @member)) @expr

(call_expression
  (navigation_expression
    (simple_identifier) @receiver
    (navigation_suffix
      (simple_identifier) @member)) @expr) @call
""",
    interface_impl_query="""
(class_declaration
  (type_identifier) @implementor
  (delegation_specifiers
    (delegation_specifier
      (user_type
        (type_identifier) @interface))))
""",
)

# =============================================================================
# SCALA
# =============================================================================

SCALA_CONFIG = LanguageQueryConfig(
    language_family="jvm",
    grammar_name="scala",
    scope_node_types=["function_definition", "class_definition"],
    member_access_types=["field_expression"],
    optional_patterns=["Option["],
    array_patterns=["List[", "Seq[", "Array[", "Set[", "Vector["],
    generic_indicator="[",
    supports_interfaces=True,
    type_annotation_query="""
; Function parameters
(parameter
  name: (identifier) @name
  (parameter_type
    (_) @type)) @param

; Function return types
(function_definition
  name: (identifier) @name
  return_type: (_) @type) @return

; Val/var declarations
(val_definition
  pattern: (identifier) @name
  type: (_) @type)

(var_definition
  pattern: (identifier) @name
  type: (_) @type)
""",
    type_member_query="""
; Class methods
(class_definition
  name: (identifier) @parent
  body: (template_body
    (function_definition
      name: (identifier) @member) @method))

; Class vals/vars
(class_definition
  name: (identifier) @parent
  body: (template_body
    (val_definition
      pattern: (identifier) @member
      type: (_) @type?)))

; Trait methods
(trait_definition
  name: (identifier) @parent
  body: (template_body
    (function_definition
      name: (identifier) @member) @method))
""",
    member_access_query="""
(field_expression
  value: (identifier) @receiver
  field: (identifier) @member) @expr

(call_expression
  function: (field_expression
    value: (identifier) @receiver
    field: (identifier) @member) @expr) @call
""",
    interface_impl_query="""
(class_definition
  name: (identifier) @implementor
  (extends_clause
    (type_identifier) @interface))
""",
)

# =============================================================================
# DART
# =============================================================================

DART_CONFIG = LanguageQueryConfig(
    language_family="dart",
    grammar_name="dart",
    scope_node_types=["function_signature", "method_signature", "class_definition"],
    member_access_types=["selector"],
    optional_patterns=["?"],
    array_patterns=["List<", "Iterable<", "Set<"],
    generic_indicator="<",
    supports_interfaces=True,
    type_annotation_query="""
; Function parameters
(formal_parameter
  (type_identifier) @type
  (identifier) @name) @param

; Function return types
(function_signature
  (type_identifier) @type
  name: (identifier) @name) @return

(method_signature
  (type_identifier) @type
  name: (identifier) @name) @return

; Variable declarations
(initialized_variable_definition
  (type_identifier) @type
  name: (identifier) @name)
""",
    type_member_query="""
; Class methods
(class_definition
  name: (identifier) @parent
  body: (class_body
    (method_signature
      name: (identifier) @member) @method))

; Class fields
(class_definition
  name: (identifier) @parent
  body: (class_body
    (declaration
      (type_identifier) @type
      (identifier) @member)))
""",
    member_access_query="""
(selector
  (unconditional_assignable_selector
    (identifier) @member)) @expr

; More complex patterns would need grammar inspection
""",
    interface_impl_query="""
(class_definition
  name: (identifier) @implementor
  (interfaces
    (type_identifier) @interface))
""",
)

# =============================================================================
# ELIXIR
# =============================================================================

ELIXIR_CONFIG = LanguageQueryConfig(
    language_family="elixir",
    grammar_name="elixir",
    scope_node_types=["call"],  # def, defp, defmodule are calls
    member_access_types=["dot"],
    optional_patterns=[],
    array_patterns=["list(", "[]"],
    generic_indicator="",
    supports_type_annotations=True,  # @spec
    supports_interfaces=True,  # @behaviour
    type_annotation_query="""
; @spec function_name(arg_types) :: return_type
; This is complex in Elixir due to @spec being an attribute
; Simplified pattern:
(unary_operator
  operator: "@"
  operand: (call
    target: (identifier) @kind
    (arguments
      (binary_operator
        left: (call
          target: (identifier) @name)
        operator: "::"
        right: (_) @type)))) @return
""",
    type_member_query="""
; Module functions
(call
  target: (identifier) @defmodule
  (arguments
    (alias) @parent)
  (do_block
    (call
      target: (identifier) @def_kind
      (arguments
        (call
          target: (identifier) @member)))))) @method
""",
    member_access_query="""
(dot
  left: (identifier) @receiver
  right: (identifier) @member) @expr

(call
  target: (dot
    left: (identifier) @receiver
    right: (identifier) @member) @expr) @call
""",
    interface_impl_query="""
; @behaviour ModuleName
(unary_operator
  operator: "@"
  operand: (call
    target: (identifier) @behaviour
    (arguments
      (alias) @interface)))
""",
)

# =============================================================================
# HASKELL
# =============================================================================

HASKELL_CONFIG = LanguageQueryConfig(
    language_family="haskell",
    grammar_name="haskell",
    scope_node_types=["function", "signature"],
    member_access_types=[],  # Haskell doesn't have OO-style member access
    optional_patterns=["Maybe"],
    array_patterns=["[]", "List"],
    generic_indicator="",  # Haskell uses type variables, not generics syntax
    supports_type_annotations=True,
    supports_interfaces=True,  # Type classes
    type_annotation_query="""
; Type signatures: functionName :: Type -> Type
(signature
  name: (variable) @name
  type: (_) @type) @return

; Pattern type annotations (less common)
(typed_expression
  expression: (variable) @name
  type: (_) @type)
""",
    type_member_query="""
; Class methods
(class
  name: (type) @parent
  (class_body
    (signature
      name: (variable) @member) @method))

; Data constructor fields (record syntax)
(data
  name: (type) @parent
  (constructors
    (data_constructor
      (record
        (field
          (variable) @member
          type: (_) @type)))))
""",
    member_access_query="""
; Record field access
(variable) @receiver

; Haskell uses functions for field access, not . syntax typically
""",
    interface_impl_query="""
; instance TypeClass Type where
(instance
  name: (type) @interface
  types: (type) @implementor)
""",
)

# =============================================================================
# OCAML
# =============================================================================

OCAML_CONFIG = LanguageQueryConfig(
    language_family="ocaml",
    grammar_name="ocaml",
    scope_node_types=["value_definition", "let_binding"],
    member_access_types=["field_get_expression"],
    optional_patterns=["option"],
    array_patterns=["list", "array"],
    generic_indicator="'",  # Type variables use '
    supports_type_annotations=True,
    supports_interfaces=True,  # Module signatures
    type_annotation_query="""
; Let bindings with type annotation
(let_binding
  pattern: (value_name) @name
  type: (type_constructor_path) @type)

; Function parameters with type
(parameter
  pattern: (value_name) @name
  type: (_) @type) @param
""",
    type_member_query="""
; Record fields
(type_definition
  (type_binding
    name: (type_constructor) @parent
    body: (record_declaration
      (field_declaration
        name: (field_name) @member
        type: (_) @type))))

; Object methods (less common)
(object_expression
  (method_definition
    name: (method_name) @member) @method)
""",
    member_access_query="""
(field_get_expression
  record: (value_path) @receiver
  field: (field_path) @member) @expr
""",
    interface_impl_query="",  # OCaml uses module signatures differently
)

# =============================================================================
# ZIG
# =============================================================================

ZIG_CONFIG = LanguageQueryConfig(
    language_family="zig",
    grammar_name="zig",
    scope_node_types=["fn_decl"],
    member_access_types=["field_access"],
    optional_patterns=["?"],
    array_patterns=["[]"],
    generic_indicator="",
    reference_indicator="*",
    supports_type_annotations=True,
    supports_interfaces=False,  # Zig doesn't have interfaces
    type_annotation_query="""
; Function parameters
(param_decl
  name: (identifier) @name
  type: (_) @type) @param

; Function return types
(fn_decl
  name: (identifier) @name
  return_type: (_) @type) @return

; Variable declarations
(var_decl
  name: (identifier) @name
  type: (_) @type)

(const_decl
  name: (identifier) @name
  type: (_) @type)
""",
    type_member_query="""
; Struct fields
(container_decl
  (container_field
    name: (identifier) @member
    type: (_) @type))
""",
    member_access_query="""
(field_access
  operand: (identifier) @receiver
  field: (identifier) @member) @expr

(call_expr
  function: (field_access
    operand: (identifier) @receiver
    field: (identifier) @member) @expr) @call
""",
)

# =============================================================================
# NIM
# =============================================================================

NIM_CONFIG = LanguageQueryConfig(
    language_family="nim",
    grammar_name="nim",
    scope_node_types=["proc_declaration", "func_declaration"],
    member_access_types=["dot_expr"],
    optional_patterns=["Option["],
    array_patterns=["seq[", "array["],
    generic_indicator="[",
    reference_indicator="ref",
    supports_type_annotations=True,
    supports_interfaces=False,
    type_annotation_query="""
; Proc parameters
(param
  (symbol_declaration) @name
  (type_expression) @type) @param

; Proc return types
(proc_declaration
  name: (symbol_declaration) @name
  return_type: (type_expression) @type) @return

; Let/var declarations
(let_section
  (variable_declaration
    (symbol_declaration) @name
    (type_expression) @type?))

(var_section
  (variable_declaration
    (symbol_declaration) @name
    (type_expression) @type?))
""",
    type_member_query="""
; Object fields
(type_section
  (type_declaration
    name: (symbol_declaration) @parent
    (object_declaration
      (field_declaration
        (symbol_declaration) @member
        (type_expression) @type?))))
""",
    member_access_query="""
(dot_expr
  left: (identifier) @receiver
  right: (identifier) @member) @expr

(call_expr
  function: (dot_expr
    left: (identifier) @receiver
    right: (identifier) @member) @expr) @call
""",
)

# =============================================================================
# REGISTRY OF ALL CONFIGS
# =============================================================================

ALL_LANGUAGE_CONFIGS: dict[str, LanguageQueryConfig] = {
    "python": PYTHON_CONFIG,
    "javascript": TYPESCRIPT_CONFIG,
    "typescript": TYPESCRIPT_CONFIG,
    "go": GO_CONFIG,
    "rust": RUST_CONFIG,
    "java": JAVA_CONFIG,
    "jvm": JAVA_CONFIG,
    "csharp": CSHARP_CONFIG,
    "dotnet": CSHARP_CONFIG,
    "cpp": CPP_CONFIG,
    "c": CPP_CONFIG,
    "swift": SWIFT_CONFIG,
    "ruby": RUBY_CONFIG,
    "php": PHP_CONFIG,
    "kotlin": KOTLIN_CONFIG,
    "scala": SCALA_CONFIG,
    "dart": DART_CONFIG,
    "elixir": ELIXIR_CONFIG,
    "haskell": HASKELL_CONFIG,
    "ocaml": OCAML_CONFIG,
    "zig": ZIG_CONFIG,
    "nim": NIM_CONFIG,
}


def get_config_for_language(language: str) -> LanguageQueryConfig | None:
    """Get the query config for a language."""
    return ALL_LANGUAGE_CONFIGS.get(language.lower())
