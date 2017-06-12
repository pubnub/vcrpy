import functools

from twisted.internet import reactor
from twisted.internet.defer import Deferred
from twisted.internet.protocol import Protocol
from twisted.python.failure import Failure
from twisted.web.client import ResponseDone
from twisted.web.client import Agent
from twisted.web.http_headers import Headers

from vcr.request import Request
from vcr.errors import CannotOverwriteExistingCassetteException


class TwistedResponse(object):
    def __init__(self, body, code):
        self.body = body
        self.code = code


class VCRResponse(Protocol):
    def __init__(self, response_string, code, headers):
        self.response_string = response_string
        self.code = code
        self.headers = headers
        self._body = response_string
        self.length = len(self.response_string)

    def deliverBody(self, protocol):
        # protocol.dataReceived(self.response_string)
        # protocol.connectionLost(None)
        for chunk in self._body:
            protocol.dataReceived(chunk)
        protocol.connectionLost(Failure(ResponseDone()))


class RealResponse(Protocol):
    def __init__(self, finished, code):
        self.finished = finished
        self.code = code

    def dataReceived(self, body):
        self.finished.callback(TwistedResponse(body, self.code))


def new_vcr_request(cassette, real_request_func):

    @functools.wraps(real_request_func)
    def vcr_request(self, method, uri, headers=None, bodyProducer=None):
        d = Deferred()

        # TODO: get body
        vcr_request = Request(
            method,
            uri,
            '',
            headers._rawHeaders,
        )

        response = None

        if cassette.can_play_response_for(vcr_request):
            vcr_response = cassette.play_response(vcr_request)

            # recorded_headers = vcr_response['headers']
            # if isinstance(recorded_headers, Headers):
            #     print("recorded_headers._rawHeaders")
            #     print(recorded_headers._rawHeaders)
            #     recorded_headers = recorded_headers._rawHeaders
            # for k, vs in recorded_headers:
            #     for v in vs:
            #         headers.add(k, v)
            response = VCRResponse(
                code=vcr_response['status']['code'],
                response_string=vcr_response['body']['string'],
                headers=vcr_response['headers']
            )


        else:
            if cassette.write_protected and cassette.filter_request(
                vcr_request
            ):
                print(  "No match for the request (%r) was found. "
                        "Can't overwrite existing cassette (%r) in "
                        "your current record mode (%r)."
                        % (vcr_request, cassette._path, cassette.record_mode))
                response = VCRResponse(
                    code=599,
                    response_string=str(CannotOverwriteExistingCassetteException(
                        "No match for the request (%r) was found. "
                        "Can't overwrite existing cassette (%r) in "
                        "your current record mode (%r)."
                        % (vcr_request, cassette._path, cassette.record_mode)
                    )),
                    headers=Headers({})
                )
            else:
                def received(response):
                    try:
                        finished = Deferred()
                        response.deliverBody(RealResponse(finished, response.code))
                        return finished
                    except Exception as ex:
                        print("Receiving failed!")
                        print(ex)

                def record_cassette(response):
                    try:
                        vcr_response = {
                            'status': {
                                'code': response.code,
                                'message': '',
                            },
                            'headers': headers,
                            'body': {'string': response.body},
                            'url': uri,
                        }
                        cassette.append(vcr_request, vcr_response)
                        return response
                    except Exception as ex:
                        print("Recording failed!")
                        print(ex)


                agent = Agent(reactor)

                request = real_request_func(
                    agent,
                    method,
                    uri,
                    headers,
                    bodyProducer
                )

                request.addCallback(received)
                request.addCallback(record_cassette)

        reactor.callLater(0, d.callback, response)
        return d

    return vcr_request
