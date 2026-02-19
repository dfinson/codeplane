"""Second file that imports from the target — tests cross-file refactoring."""

from tests._pressure_test_refactor_target import (
    pressure_test_func_alpha,
    PressureTestClassBeta,
)


def use_imports():
    val = pressure_test_func_alpha()
    obj = PressureTestClassBeta()
    return val, obj
