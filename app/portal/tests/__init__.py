import logging

# Setup events are logged on stderr. Failure-path tests log expected warnings and errors, and they bury the test and
# installer output. The tests assert outcomes, so a real failure still fails the test.
logging.getLogger('portal.setup').setLevel(logging.CRITICAL)
