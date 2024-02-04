cd "$(dirname "$0")"

black -l 110 *.py && mypy *.py && pylint --disable=arguments-out-of-order,too-many-instance-attributes,line-too-long,invalid-name,too-many-branches,broad-exception-caught,missing-function-docstring,missing-module-docstring,missing-class-docstring,raise-missing-from,too-many-boolean-expressions,too-many-nested-blocks,too-many-statements,too-many-locals *.py
