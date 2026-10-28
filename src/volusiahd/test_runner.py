"""A test runner that keeps the log out of the way until a test fails (#26).

Plenty of tests exercise something that logs on purpose: the admin refusing an
editor, a 404, the geocoder being down, every Google sign-in. Printed as they
happen, those filled the CI output with tracebacks that looked like failures
and were not. This captures what each test logs and prints it only alongside a
failure or error, which is the one time it helps.
"""

import logging
import unittest
import warnings

from django.test.runner import DiscoverRunner


class _Capture(logging.Handler):
    """Holds formatted log lines for the test that is running."""

    def __init__(self):
        super().__init__()
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


_capture = _Capture()


class LogCapturingResult(unittest.TextTestResult):
    """Adds a failing test's log to its report, and drops a passing one's."""

    def startTest(self, test):
        _capture.lines.clear()
        super().startTest(test)

    def addError(self, test, err):
        super().addError(test, err)
        self._attach(self.errors)

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._attach(self.failures)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            failed = issubclass(err[0], test.failureException)
            self._attach(self.failures if failed else self.errors)

    @staticmethod
    def _attach(reports):
        if not _capture.lines or not reports:
            return
        test, text = reports[-1]
        logged = "\n".join(_capture.lines)
        reports[-1] = (test, f"{text}\nLogged during the test:\n{logged}\n")


class QuietTestRunner(DiscoverRunner):
    def get_resultclass(self):
        # --debug-sql and --pdb bring result classes of their own; leave those be.
        return super().get_resultclass() or LogCapturingResult

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        root = logging.getLogger()
        self._saved_handlers = root.handlers[:]
        root.handlers = [_capture]
        # WhiteNoise warns that `collectstatic` has not been run. Tests never
        # run it; the deploy build does.
        warnings.filterwarnings("ignore", message="No directory at: ", category=UserWarning)

    def teardown_test_environment(self, **kwargs):
        logging.getLogger().handlers = self._saved_handlers
        super().teardown_test_environment(**kwargs)
