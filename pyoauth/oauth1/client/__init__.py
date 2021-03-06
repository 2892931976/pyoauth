#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2011 Yesudeep Mangalapilly <yesudeep@gmail.com>
# Copyright 2012 Google, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.


from __future__ import absolute_import

import logging

from mom.codec.text import utf8_encode, utf8_decode_if_bytes
from mom.functional import partition_dict, map_dict

from pyoauth.constants import \
    OAUTH_PARAM_VERSION, OAUTH_PARAM_SIGNATURE, OAUTH_PARAM_TOKEN, \
    OAUTH_PARAM_SIGNATURE_METHOD, HEADER_AUTHORIZATION, HTTP_GET, \
    HEADER_CONTENT_LENGTH_CAPS, HEADER_CONTENT_LENGTH, \
    HEADER_AUTHORIZATION_CAPS, HEADER_CONTENT_TYPE, \
    SYMBOL_EMPTY_BYTES, SYMBOL_ZERO, OAUTH_VERSION_1, \
    OAUTH_PARAM_PREFIX, OAUTH_PARAM_CALLBACK_CONFIRMED, \
    OAUTH_VALUE_CALLBACK_CONFIRMED, OAUTH_PARAM_TOKEN_SECRET, \
    HTTP_POST, OAUTH_VALUE_CALLBACK_OOB, OAUTH_PARAM_CALLBACK, \
    HEADER_CONTENT_TYPE_CAPS
from pyoauth.http import CONTENT_TYPE_FORM_URLENCODED, RequestAdapter
from pyoauth.error import \
    InvalidAuthorizationHeaderError, InvalidSignatureMethodError, \
    IllegalArgumentError, InvalidHttpRequestError, \
    InvalidContentTypeError, HttpError, InvalidHttpResponseError, \
    SignatureMethodNotSupportedError
from pyoauth.oauth1 import \
    SIGNATURE_METHOD_HMAC_SHA1, \
    SIGNATURE_METHOD_RSA_SHA1, \
    SIGNATURE_METHOD_PLAINTEXT, Credentials
from pyoauth.oauth1.protocol import \
    generate_authorization_header, \
    generate_base_string, \
    generate_nonce, \
    generate_timestamp, \
    generate_hmac_sha1_signature, \
    generate_rsa_sha1_signature, \
    generate_plaintext_signature
from pyoauth.url import \
    url_append_query, url_add_query, \
    query_append, request_query_remove_non_oauth, \
    oauth_url_sanitize, is_valid_callback_url, query_remove_oauth, \
    parse_qs, query_add


SIGNATURE_METHOD_MAP = {
    SIGNATURE_METHOD_HMAC_SHA1: generate_hmac_sha1_signature,
    SIGNATURE_METHOD_RSA_SHA1: generate_rsa_sha1_signature,
    SIGNATURE_METHOD_PLAINTEXT: generate_plaintext_signature,
}


class _OAuthClient(object):
    def __init__(self, client_credentials, http_client,
                 use_authorization_header=True):
        self._client_credentials = client_credentials
        self._http_client = http_client
        self._use_authorization_header = use_authorization_header

    @property
    def oauth_version(self):
        return OAUTH_VERSION_1

    @classmethod
    def generate_nonce(cls):
        """
        Generates a nonce value.
        Override if you need a different method.
        """
        return generate_nonce()

    @classmethod
    def generate_timestamp(cls):
        """
        Generates a timestamp.
        Override if you need a different method.
        """
        return generate_timestamp()

    @classmethod
    def check_signature_method(cls, signature_method):
        """Override this if you need to check your signature method.
        Should raise an error if the method is not supported."""
        if signature_method not in SIGNATURE_METHOD_MAP:
            raise SignatureMethodNotSupportedError(
                "OAuth 1.0 does not support the `%r` signature method." % \
                signature_method
            )

    @classmethod
    def _generate_oauth_params(cls,
                               oauth_consumer_key,
                               oauth_signature_method,
                               oauth_version,
                               oauth_nonce,
                               oauth_timestamp,
                               oauth_token,
                               **extra_oauth_params):
        """
        Generates properly formatted ``oauth_params`` dictionary for use with an
        OAuth request.

        :param oauth_consumer_key:
            Your OAuth consumer key (client identifier).
        :param oauth_signature_method:
            The signature method to use.
        :param oauth_version:
            The version of OAuth to be used. "1.0" for standards-compliant.
        :param oauth_nonce:
            A unique randomly generated nonce value.
        :param oauth_timestamp:
            A unique timestamp since epoch.
        :param oauth_token:
            A response oauth_token if obtained from the OAuth server.
        :returns:
            A dictionary of protocol parameters.
        """
        if oauth_signature_method not in SIGNATURE_METHOD_MAP:
            raise InvalidSignatureMethodError(
                "Invalid signature method specified: %r" % \
                oauth_signature_method
            )

        # Reserved OAuth parameters.
        oauth_params = dict(
            oauth_consumer_key=oauth_consumer_key,
            oauth_signature_method=oauth_signature_method,
            oauth_timestamp=oauth_timestamp,
            oauth_nonce=oauth_nonce,
            )
        # If we have an oauth token.
        if oauth_token:
            oauth_params[OAUTH_PARAM_TOKEN] = oauth_token
        # If we have a version.
        if oauth_version:
            oauth_params[OAUTH_PARAM_VERSION] = oauth_version

        # Clean up oauth parameters in the arguments.
        extra_oauth_params = request_query_remove_non_oauth(extra_oauth_params)
        for k, v in extra_oauth_params.items():
            if k == OAUTH_PARAM_SIGNATURE:
                raise IllegalArgumentError("Cannot override system-generated "\
                                           "protocol parameter: %r" % k)
            else:
                oauth_params[k] = v[0]
        return oauth_params

    @classmethod
    def _generate_signature(cls, method, url, params,
                            body, headers,
                            oauth_consumer_secret,
                            oauth_token_secret,
                            oauth_params):
        """
        Given the base string parameters, secrets, and protocol parameters,
        calculates a signature for the request.

        :param method:
            HTTP method.
        :param url:
            Request URL.
        :param params:
            Additional query/payload parameters.
        :param body:
            Payload if any.
        :param headers:
            HTTP headers as a dictionary.
        :param oauth_consumer_secret:
            OAuth client shared secret (consumer secret).
        :param oauth_token_secret:
            OAuth token/temporary shared secret if obtained from the OAuth
            server.
        :param oauth_params:
            OAuth parameters generated by
            :func:`OAuthClient._generate_oauth_params`.
        :returns:
            Request signature.
        """
        # Take parameters from the body if the Content-Type is specified
        # as ``application/x-www-form-urlencoded``.
        # http://tools.ietf.org/html/rfc5849#section-3.4.1.3.1
        if body:
            try:
                try:
                    content_type = headers[HEADER_CONTENT_TYPE]
                except KeyError:
                    content_type = headers[HEADER_CONTENT_TYPE_CAPS]

                if content_type == CONTENT_TYPE_FORM_URLENCODED:
                    # These parameters must also be included in the signature.
                    # Ignore OAuth-specific parameters. They must be specified
                    # separately.
                    body_params = query_remove_oauth(parse_qs(body))
                    params = query_add(params, body_params)
                else:
                    logging.info(
                        "Entity-body specified but `content-type` header " \
                        "value is not %r: entity-body parameters if " \
                        "present will not be signed: got body %r" % \
                        (CONTENT_TYPE_FORM_URLENCODED, body)
                    )
            except KeyError:
                logging.warning(
                    "Entity-body specified but `content-type` is missing "
                )

        # Make oauth params and sign the request.
        signature_url = url_add_query(url, query_remove_oauth(params))
        # NOTE: We're not explicitly cleaning up because this method
        # expects oauth params generated by _generate_oauth_params.
        base_string = generate_base_string(method, signature_url, oauth_params)

        signature_method = oauth_params[OAUTH_PARAM_SIGNATURE_METHOD]
        cls.check_signature_method(signature_method)
        try:
            sign_func = SIGNATURE_METHOD_MAP[signature_method]
            return sign_func(base_string,
                             oauth_consumer_secret,
                             oauth_token_secret)
        except KeyError:
            raise InvalidSignatureMethodError(
                "unsupported signature method: %r" % signature_method
            )

    @classmethod
    def _build_request(cls, method, url, params, body, headers,
                       oauth_params, realm, use_authorization_header):
        """
        Builds a request based on the HTTP arguments and OAuth protocol
        parameters.

        :param method:
            HTTP method.
        :param url:
            Request URL
        :param params:
            Additional query/payload parameters.
            If a `body` argument to this function is specified,
            the parameters are appended to the URL query string.
            If a `body` is not specified and a method other than GET is used
            the parameters will be added to the entity body.
        :param body:
            Entity body.
        :param oauth_params:
            Protocol-specific parameters.
        :param realm:
            OAuth authorization realm.
        :param use_authorization_header:
            ``True`` if the Authorization HTTP header should be used;
            ``False`` otherwise.
        :returns:
            An instance of :class:`pyoauth.http.RequestAdapter`.
        """
        # http://tools.ietf.org/html/rfc5849#section-3.6
        if HEADER_AUTHORIZATION_CAPS in headers or \
           HEADER_AUTHORIZATION in headers:
            raise InvalidAuthorizationHeaderError(
                "Authorization field is already present in headers: %r" % \
                headers
            )
        if use_authorization_header:
            headers[HEADER_AUTHORIZATION_CAPS] = \
                generate_authorization_header(oauth_params, realm)
            # Empty oauth params so that they are not included again below.
            oauth_params = None

        # OAuth requests can contain payloads.
        if body or method == HTTP_GET:
            # Append params to query string.
            url = url_append_query(url_add_query(url, params), oauth_params)
            if body and method == HTTP_GET:
                raise InvalidHttpRequestError(
                    "HTTP method GET does not take an entity body"
                )
            if body and \
               HEADER_CONTENT_LENGTH not in headers and \
               HEADER_CONTENT_LENGTH_CAPS not in headers:
                raise ValueError("You must set the `content-length` header.")
        else:
            if params or oauth_params:
                # Append to payload and set content type.
                body = utf8_encode(query_append(params, oauth_params))
                headers[HEADER_CONTENT_TYPE] = CONTENT_TYPE_FORM_URLENCODED
                headers[HEADER_CONTENT_LENGTH] = str(len(body)).encode("ascii")
            else:
                # Zero-length body.
                body = SYMBOL_EMPTY_BYTES
                headers[HEADER_CONTENT_LENGTH] = SYMBOL_ZERO
        return RequestAdapter(method, url, body, headers)

    @classmethod
    def _request(cls,
                 client_credentials,
                 method, url, params=None, body=None, headers=None,
                 realm=None, use_authorization_header=True,
                 auth_credentials=None,
                 oauth_signature_method=SIGNATURE_METHOD_HMAC_SHA1,
                 oauth_version=OAUTH_VERSION_1,
                 **kwargs):
        """
        Makes an OAuth request.

        :param client_credentials:
            Client credentials (consumer key and secret).
        :param method:
            HTTP method.
        :param url:
            Request URL
        :param params:
            Additional query/payload parameters.
            If a `body` argument to this function is specified,
            the parameters are appended to the URL query string.
            If a `body` is not specified and a method other than GET is used
            the parameters will be added to the entity body.
        :param body:
            Entity body string.
        :param headers:
            Request headers dictionary.
        :param realm:
            Authorization realm.
        :param use_authorization_header:
            ``True`` if we should; ``False`` otherwise.
        :param auth_credentials:
            OAuth token/temporary credentials (if available).
        :param oauth_signature_method:
            Signature method.
        :param kwargs:
            Additional parameters including those that may begin with
            ``oauth_``.
        :returns:
            HTTP response (:class:`pyoauth.http.ResponseAdapter`) if
            ``async_callback`` is not specified;
            otherwise, ``async_callback`` is called with the response as its
            argument.
        """
        method = method.upper()
        body = body or SYMBOL_EMPTY_BYTES
        headers = headers or {}

        # Split all the oauth parameters and function parameters.
        extra_oauth_params, kwargs = \
            partition_dict(lambda k, v: k.startswith(OAUTH_PARAM_PREFIX),
                           kwargs)

        # Query/payload parameters must not contain OAuth-specific parameters.
        params = query_remove_oauth(params) if params else {}

        # The URL must not contain OAuth-specific parameters.
        url = oauth_url_sanitize(url, force_secure=False)

        # Temporary credentials requests don't have ``oauth_token``.
        if auth_credentials:
            oauth_token = auth_credentials.identifier
            oauth_token_secret = auth_credentials.shared_secret
        else:
            oauth_token = oauth_token_secret = None

        # Make OAuth-specific parameter dictionary.

        oauth_params = cls._generate_oauth_params(
            oauth_consumer_key=client_credentials.identifier,
            oauth_signature_method=oauth_signature_method,
            oauth_version=oauth_version,
            oauth_timestamp=cls.generate_timestamp(),
            oauth_nonce=cls.generate_nonce(),
            oauth_token=oauth_token,
            **extra_oauth_params
        )

        # Sign the request.
        signature = cls._generate_signature(method, url, params, body, headers,
                                            client_credentials.shared_secret,
                                            oauth_token_secret,
                                            oauth_params)
        oauth_params[OAUTH_PARAM_SIGNATURE] = signature

        # Now build the request.
        return cls._build_request(
            method, url, params, body, headers,
            oauth_params, realm, use_authorization_header
        )

    def _fetch(self,
              method, url, params=None, body=None, headers=None,
              async_callback=None,
              realm=None,
              auth_credentials=None,
              oauth_signature_method=SIGNATURE_METHOD_HMAC_SHA1,
              **kwargs):
        """
        Makes an OAuth request.

        :param method:
            HTTP method.
        :param url:
            Request URL
        :param params:
            Additional query/payload parameters.
            If a `body` argument to this function is specified,
            the parameters are appended to the URL query string.
            If a `body` is not specified and a method other than GET is used
            the parameters will be added to the entity body.
        :param body:
            Entity body string.
        :param headers:
            Request headers dictionary.
        :param async_callback:
            If the HTTP client used is asynchronous, then this parameter
            will be used as a callback function with the response as its
            argument.
        :param realm:
            Authorization realm.
        :param auth_credentials:
            OAuth token/temporary credentials (if available).
        :param oauth_signature_method:
            Signature method.
        :param kwargs:
            Additional parameters including those that may begin with
            ``oauth_``.
        :returns:
            HTTP response (:class:`pyoauth.http.ResponseAdapter`) if
            ``async_callback`` is not specified;
            otherwise, ``async_callback`` is called with the response as its
            argument.
        """
        request = self._request(
            self._client_credentials,
            method, url, params,
            body, headers, realm, self._use_authorization_header,
            auth_credentials,
            oauth_signature_method,
            self.oauth_version,
            **kwargs
        )
        return self._http_client.fetch(request, async_callback)

    @classmethod
    def check_verification_code(cls,
                                temporary_credentials,
                                oauth_token, oauth_verifier):
        """
        When an OAuth 1.0 server redirects the resource owner to your
        callback URL after authorization, it will attach two parameters to
        the query string.

        1. ``oauth_token``: Must match your temporary credentials identifier.
        2. ``oauth_verifier``: Server-generated verification code that you will
           use in the next step--that is requesting token credentials.

        :param temporary_credentials:
            Temporary credentials
        :param oauth_token:
            The value of the ``oauth_token`` parameter as obtained
            from the server redirect.
        :param oauth_verifier:
            The value of the ``oauth_verifier`` parameter as obtained
            from the server redirect.
        """
        if temporary_credentials.identifier != oauth_token:
            raise InvalidHttpRequestError(
                "OAuth token returned in callback query `%r` " \
                "does not match temporary credentials: `%r`" % \
                (oauth_token, temporary_credentials.identifier)
            )

    @classmethod
    def parse_temporary_credentials_response(cls, response, strict=True):
        """
        Parses the entity-body of the OAuth server response to an OAuth
        temporary credentials request.

        :param response:
            An instance of :class:`pyoauth.http.ResponseAdapter`.
        :param strict:
            ``True`` (default) for string response parsing; ``False`` to be a
            bit lenient. Some non-compliant OAuth servers return credentials
            without setting the content-type.

            Setting this to ``False`` will not raise an error, but will
            still warn you that the response content-type is not valid.
            The temporary credentials response also expects
            "oauth_callback_confirmed=true" in the response body, checking for
            this is disabled when you set this argument to ``False``.
        :returns:
            A tuple of the form::

                (pyoauth.oauth1.Credentials instance, other parameters)
        """
        credentials, params = cls._parse_credentials_response(response, strict)

        # The OAuth specification mandates that this parameter must be set to
        # `"true"`; otherwise, the response is invalid.
        if params.get(
            OAUTH_PARAM_CALLBACK_CONFIRMED,
            [SYMBOL_EMPTY_BYTES])[0].lower() != OAUTH_VALUE_CALLBACK_CONFIRMED:
            if strict:
                raise ValueError(
                    "Invalid OAuth server response -- " \
                    "`oauth_callback_confirmed` MUST be set to `true`.")
            else:
                logging.warning(
                    "Response parsing strict-mode disabled -- " \
                    "OAuth server credentials response specifies invalid " \
                    "`oauth_callback_confirmed` value: expected `true`; " \
                    "got %r" % params
                )

        return credentials, params

    @classmethod
    def parse_token_credentials_response(cls, response, strict=True):
        """
        Parses the entity-body of the OAuth server response to an OAuth
        token credentials request.

        :param response:
            An instance of :class:`pyoauth.http.ResponseAdapter`.
        :param strict:
            ``True`` (default) for string response parsing; ``False`` to be a
            bit lenient. Some non-compliant OAuth servers return credentials
            without setting the content-type.

            Setting this to ``False`` will not raise an error, but will
            still warn you that the response content-type is not valid.
        :returns:
            A tuple of the form::

                (pyoauth.oauth1.Credentials instance, other parameters)
        """
        return cls._parse_credentials_response(response, strict)

    @classmethod
    def _parse_credentials_response(cls, response, strict=True):
        """
        Parses the entity-body of the OAuth server response to an OAuth
        credential request.

        :param response:
            An instance of :class:`pyoauth.http.ResponseAdapter`.
        :param strict:
            ``True`` (default) for string response parsing; ``False`` to be a
            bit lenient. Some non-compliant OAuth servers return credentials
            without setting the content-type.

            Setting this to ``False`` will not raise an error, but will
            still warn you that the response content-type is not valid.
        :returns:
            A tuple of the form::

                (pyoauth.oauth1.Credentials instance, other parameters)
        """
        if not response.status:
            raise InvalidHttpResponseError(
                "Invalid status code: `%r`" % response.status)
        if not response.reason:
            raise InvalidHttpResponseError(
                "Invalid status message: `%r`" % response.reason)
        if not response.body:
            raise InvalidHttpResponseError(
                "Body is invalid or empty: `%r`" % response.body)
        if not response.headers:
            raise InvalidHttpResponseError(
                "Headers are invalid or not specified: `%r`" % \
                response.headers)

        if response.error:
            raise HttpError("Could not fetch credentials: HTTP %d - %s" \
            % (response.status, response.reason,))

        # The response body must be form URL-encoded.
        if not response.is_body_form_urlencoded():
            if strict:
                raise InvalidContentTypeError(
                    "OAuth credentials server response must " \
                    "have Content-Type: `%s`; got %r" %
                    (CONTENT_TYPE_FORM_URLENCODED, response.content_type))
            else:
                logging.warning(
                    "Response parsing strict-mode disabled -- " \
                    "OAuth server credentials response specifies invalid " \
                    "Content-Type: expected %r; got %r",
                    CONTENT_TYPE_FORM_URLENCODED, response.content_type)

        params = parse_qs(response.body)
        # Ensure the keys to this dictionary are unicode strings in Python 3.x.
        params = map_dict(lambda k, v: (utf8_decode_if_bytes(k), v), params)
        credentials = Credentials(identifier=params[OAUTH_PARAM_TOKEN][0],
                            shared_secret=params[OAUTH_PARAM_TOKEN_SECRET][0])
        return credentials, params


class Client(_OAuthClient):
    def __init__(self,
                 http_client,
                 client_credentials,
                 temporary_credentials_uri,
                 token_credentials_uri,
                 authorization_uri,
                 authentication_uri=None,
                 use_authorization_header=True,
                 strict=True):
        super(Client, self).__init__(client_credentials,
                                     http_client,
                                     use_authorization_header)
        self._temporary_credentials_uri = \
            oauth_url_sanitize(temporary_credentials_uri)
        self._token_credentials_uri = \
            oauth_url_sanitize(token_credentials_uri)
        self._authorization_uri = \
            oauth_url_sanitize(authorization_uri, False)
        if authentication_uri:
            self._authentication_uri = \
                oauth_url_sanitize(authentication_uri, False)
        else:
            self._authentication_uri = None
        self._strict = strict

    @property
    def client_credentials(self):
        """
        Returns the client credentials associated with this client.

        :returns:
            Client credentials instance.
        """
        return self._client_credentials

    def fetch_temporary_credentials(self,
                                    method=HTTP_POST, params=None,
                                    body=None, headers=None,
                                    realm=None,
                                    async_callback=None,
                                    oauth_signature_method=\
                                        SIGNATURE_METHOD_HMAC_SHA1,
                                    oauth_callback=OAUTH_VALUE_CALLBACK_OOB,
                                    **kwargs):
        """
        Fetches temporary credentials.

        :param method:
            HTTP method.
        :param params:
            Additional query/payload parameters.
            If a `body` argument to this function is specified,
            the parameters are appended to the URL query string.
            If a `body` is not specified and a method other than GET is used
            the parameters will be added to the entity body.
        :param body:
            Entity body string.
        :param headers:
            Request headers dictionary.
        :param async_callback:
            If the HTTP client used is asynchronous, then this parameter
            will be used as a callback function with the response as its
            argument.
        :param realm:
            Authorization realm.
        :param oauth_signature_method:
            Signature method.
        :param oauth_callback:
            OAuth callback URL; default case-sensitive "oob" (out-of-band).
        :param kwargs:
            Additional parameters including those that may begin with
            ``oauth_``.
        :returns:
            HTTP response (:class:`pyoauth.http.ResponseAdapter`) if
            ``async_callback`` is not specified;
            otherwise, ``async_callback`` is called with the response as its
            argument.
        """
        if not is_valid_callback_url(oauth_callback):
            raise ValueError(
                "`%r` parameter value is invalid URL: %r" % \
                (OAUTH_PARAM_CALLBACK, oauth_callback)
            )

        if async_callback:
            def _async_callback(response):
                return async_callback(
                    *self.parse_temporary_credentials_response(response,
                                                               self._strict)
                )
        else:
            _async_callback = async_callback

        resp = self._fetch(method, self._temporary_credentials_uri, params,
                           body, headers,
                           async_callback=_async_callback,
                           realm=realm,
                           oauth_signature_method=oauth_signature_method,
                           oauth_callback=oauth_callback,
                           **kwargs)
        return self.parse_temporary_credentials_response(resp, self._strict)


    def fetch_token_credentials(self,
                                temporary_credentials,
                                oauth_verifier,
                                method=HTTP_POST, params=None,
                                body=None, headers=None,
                                realm=None, async_callback=None,
                                oauth_signature_method=\
                                    SIGNATURE_METHOD_HMAC_SHA1,
                                **kwargs):
        """
        Fetches token credentials using the temporary credentials.

        :param temporary_credentials:
            Temporary credentials obtained in a previous step.
        :param oauth_verifier:
            The oauth verification code you received after user-authorization.
        :param method:
            HTTP method.
        :param params:
            Additional query/payload parameters.
            If a `body` argument to this function is specified,
            the parameters are appended to the URL query string.
            If a `body` is not specified and a method other than GET is used
            the parameters will be added to the entity body.
        :param body:
            Entity body string.
        :param headers:
            Request headers dictionary.
        :param async_callback:
            If the HTTP client used is asynchronous, then this parameter
            will be used as a callback function with the response as its
            argument.
        :param realm:
            Authorization realm.
        :param oauth_signature_method:
            Signature method.
        :param kwargs:
            Additional parameters including those that may begin with
            ``oauth_``.
        :returns:
            HTTP response (:class:`pyoauth.http.ResponseAdapter`) if
            ``async_callback`` is not specified;
            otherwise, ``async_callback`` is called with the response as its
            argument.
        """
        if OAUTH_PARAM_CALLBACK in kwargs:
            raise IllegalArgumentError(
                '`%r` is reserved for requesting temporary '\
                'credentials only: got %r' % \
                (OAUTH_PARAM_CALLBACK, kwargs[OAUTH_PARAM_CALLBACK])
            )

        if async_callback:
            def _async_callback(response):
                return async_callback(
                    *self.parse_token_credentials_response(response,
                                                           self._strict)
                )
        else:
            _async_callback = async_callback

        response = self._fetch(method, self._token_credentials_uri, params,
                               body, headers,
                               async_callback=_async_callback,
                               realm=realm,
                               auth_credentials=temporary_credentials,
                               oauth_signature_method=oauth_signature_method,
                               oauth_verifier=oauth_verifier,
                               **kwargs)
        return self.parse_token_credentials_response(response, self._strict)

    def fetch(self,
              token_credentials,
              url, method=HTTP_POST, params=None,
              body=None, headers=None,
              realm=None, async_callback=None,
              oauth_signature_method=SIGNATURE_METHOD_HMAC_SHA1,
              **kwargs):
        """
        Fetches a resource using the token credentials.

        :param token_credentials:
            Token credentials obtained in a previous step.
        :param method:
            HTTP method.
        :param url:
            Request URL
        :param params:
            Additional query/payload parameters.
            If a `body` argument to this function is specified,
            the parameters are appended to the URL query string.
            If a `body` is not specified and a method other than GET is used
            the parameters will be added to the entity body.
        :param body:
            Entity body string.
        :param headers:
            Request headers dictionary.
        :param async_callback:
            If the HTTP client used is asynchronous, then this parameter
            will be used as a callback function with the response as its
            argument.
        :param realm:
            Authorization realm.
        :param oauth_signature_method:
            Signature method.
        :param kwargs:
            Additional parameters including any that begin with ``oauth_``.
        :returns:
            HTTP response (:class:`pyoauth.http.ResponseAdapter`) if
            ``async_callback`` is not specified;
            otherwise, ``async_callback`` is called with the response as its
            argument.
        """
        response = self._fetch(method, url, params,
                               body, headers,
                               async_callback=async_callback,
                               realm=realm,
                               auth_credentials=token_credentials,
                               oauth_signature_method=oauth_signature_method,
                               **kwargs)
        return response

    def get_authorization_url(self, temporary_credentials, **query_params):
        """
        Calculates the authorization URL to which the user will be (re)directed.

        :param temporary_credentials:
            Temporary credentials obtained after parsing the response to
            the temporary credentials request.
        :param query_params:
            Additional query parameters that you would like to include
            into the authorization URL. Parameters beginning with the ``oauth_``
            prefix will be ignored.
        """
        url = self._authorization_uri
        if query_params:
            query_params = query_remove_oauth(query_params)
            url = url_append_query(url, query_params)

        # `oauth_token` must appear last.
        return url_append_query(url, {
            OAUTH_PARAM_TOKEN: temporary_credentials.identifier,
        })

    def get_authentication_url(self, temporary_credentials, **query_params):
        """
        Calculates the automatic authentication redirect URL to which the
        user will be (re)directed. Some providers support automatic
        authorization URLs if the user is already signed in. You can use
        this method with such URLs.

        :param temporary_credentials:
            Temporary credentials obtained after parsing the response to
            the temporary credentials request.
        :param query_params:
            Additional query parameters that you would like to include
            into the authorization URL. Parameters beginning with the ``oauth_``
            prefix will be ignored.
        """
        url = self._authentication_uri
        if not url:
            raise NotImplementedError(
                "Service does not support automatic authentication redirects.")
        if query_params:
            query_params = query_remove_oauth(query_params)
            url = url_append_query(url, query_params)

        # So that the "oauth_token" appears LAST.
        return url_append_query(url, {
            OAUTH_PARAM_TOKEN: temporary_credentials.identifier,
            })
