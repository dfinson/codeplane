"""Temporary file for pressure-testing refactor_* tools."""


def pressure_test_func_omega():
    """A function that will be renamed."""
    return "alpha"


class PressureTestClassZeta:
    """A class that will be renamed."""

    def method_one(self):
        return pressure_test_func_omega()


def caller_of_alpha():
    result = pressure_test_func_omega()
    obj = PressureTestClassZeta()
    return result, obj.method_one()
