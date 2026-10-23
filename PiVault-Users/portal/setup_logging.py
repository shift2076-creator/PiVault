"""Enrollment diagnostics: fixed fields, never credentials or exception text."""
import json
import logging
from contextvars import ContextVar
from pathlib import Path
import traceback
import uuid

logger = logging.getLogger('portal.setup')
setup_id = ContextVar('setup_id', default='-')


def begin(kind):
    reference = uuid.uuid4().hex[:16]
    token = setup_id.set(reference)
    event('request_started', stage=kind)
    return reference, token


def end(token):
    setup_id.reset(token)


def event(name, *, stage=None, service=None, mode=None, status=None, error=None, level=logging.INFO):
    data = {'event': name, 'setup': setup_id.get()}
    for key, value in (('stage', stage), ('service', service), ('mode', mode), ('status', status)):
        if value is not None:
            data[key] = value
    if error is not None:
        data['error_type'] = type(error).__name__
        upstream = getattr(error, 'status', None)
        if isinstance(upstream, int):
            data['upstream_status'] = upstream
        data['uncertain'] = bool(getattr(error, 'uncertain', False))
        # Frame location only: no local variables, source text, or exception message.
        data['frames'] = [f'{Path(frame.filename).name}:{frame.lineno}:{frame.name}'
                          for frame in traceback.extract_tb(error.__traceback__)[-8:]]
    logger.log(level, json.dumps(data, separators=(',', ':')))
