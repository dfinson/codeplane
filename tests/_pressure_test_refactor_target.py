"""Temporary file for pressure-testing refactor_* tools."""


def pressure_test_func_alpha():
    """A function that will be renamed."""
    return "alpha"


class PressureTestClassBeta:
    """A class that will be renamed."""

    def method_one(self):
        return pressure_test_func_alpha()


def caller_of_alpha():
    result = pressure_test_func_alpha()
    obj = PressureTestClassBeta()
    return result, obj.method_one()
