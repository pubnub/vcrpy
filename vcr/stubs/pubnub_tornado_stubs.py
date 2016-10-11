'''Stubs for tornado HTTP clients'''
from __future__ import absolute_import

import functools
from six import BytesIO

from tornado import httputil
from tornado.concurrent import Future
from tornado.httpclient import HTTPResponse

from vcr.errors import CannotOverwriteExistingCassetteException
from vcr.request import Request


def vcr_fetch_impl(cassette, real_fetch_impl):

    @functools.wraps(real_fetch_impl)
    def new_fetch_impl(self, request, callback):
        headers = request.headers.copy()
        if request.user_agent:
            headers.setdefault('User-Agent', request.user_agent)

        # TODO body_producer, header_callback, and streaming_callback are not
        # yet supported.

        unsupported_call = (
            getattr(request, 'body_producer', None) is not None or
            request.header_callback is not None or
            request.streaming_callback is not None
        )
        if unsupported_call:
            response = HTTPResponse(
                request,
                599,
                error=Exception(
                    "The request (%s) uses AsyncHTTPClient functionality "
                    "that is not yet supported by VCR.py. Please make the "
                    "request outside a VCR.py context." % repr(request)
                ),
                request_time=self.io_loop.time() - request.start_time,
            )
            return callback(response)

        vcr_request = Request(
            request.method,
            request.url,
            request.body,
            headers,
        )

        if cassette.can_play_response_for(vcr_request):
            def new_continue_callback(user_response_callback):
                vcr_response = cassette.play_response(vcr_request)
                headers = httputil.HTTPHeaders()

                recorded_headers = vcr_response['headers']
                if isinstance(recorded_headers, dict):
                    recorded_headers = recorded_headers.items()
                for k, vs in recorded_headers:
                    for v in vs:
                        headers.add(k, v)
                response = HTTPResponse(
                    request,
                    code=vcr_response['status']['code'],
                    reason=vcr_response['status']['message'],
                    headers=headers,
                    buffer=BytesIO(vcr_response['body']['string']),
                    effective_url=vcr_response.get('url'),
                    request_time=self.io_loop.time() - request.start_time,
                )
                return user_response_callback(response)

            tornado_key_response = _TornadoStub(new_continue_callback)
            tornado_key_response.continue_callback = new_continue_callback
            return callback(tornado_key_response)
        else:
            if cassette.write_protected and cassette.filter_request(
                vcr_request
            ):
                # HACK: cancelled subscribe requests should not raise the exception:
                if vcr_request.path.startswith('/v2/subscribe'):
                    return

                def new_continue_callback(user_response_callback):
                    response = HTTPResponse(
                        request,
                        599,
                        error=CannotOverwriteExistingCassetteException(
                            "No match for the request (%r) was found. "
                            "Can't overwrite existing cassette (%r) in "
                            "your current record mode (%r)."
                            % (vcr_request, cassette._path, cassette.record_mode)
                        ),
                        request_time=self.io_loop.time() - request.start_time,
                    )
                    return user_response_callback(response)

                tornado_key_response = _TornadoStub(new_continue_callback)
                tornado_key_response.continue_callback = new_continue_callback
                callback(tornado_key_response)

            def key_callback(tornado_key_response):
                _continue_callback = tornado_key_response.continue_callback

                def new_continue_callback(user_response_callback):
                    def new_callback(response):
                        print("got response")
                        headers = [
                            (k, response.headers.get_list(k))
                            for k in response.headers.keys()
                            ]

                        vcr_response = {
                            'status': {
                                'code': response.code,
                                'message': response.reason,
                            },
                            'headers': headers,
                            'body': {'string': response.body},
                            'url': response.effective_url,
                        }
                        cassette.append(vcr_request, vcr_response)
                        return user_response_callback(response)
                    return _continue_callback(new_callback)
                tornado_key_response.continue_callback = new_continue_callback
                return callback(tornado_key_response)

            return real_fetch_impl(self, request, key_callback)

    return new_fetch_impl


class _TornadoStub(object):
    def __init__(self, after_key_callback):
        self.error = None
        self.key = None
        self.continue_callback = after_key_callback
