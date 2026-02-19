"""Second file that imports from the target — tests cross-file refactoring."""

from tests._pressure_test_refactor_target import (
    pressure_test_func_omega,
    PressureTestClassZeta,
)


def use_imports():
    val = pressure_test_func_omega()
    obj = PressureTestClassZeta()
    return val, obj
