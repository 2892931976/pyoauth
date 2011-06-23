#!/usr/bin/env python
# -*- coding: utf-8 -*-
# OAuth utility functions.
#
# Copyright (C) 2009 Facebook.
# Copyright (C) 2011 Yesudeep Mangalapilly <yesudeep@gmail.com>


import binascii
import hashlib
import hmac
import time
import urlparse
import urllib
import uuid

try:
    from urlparse import parse_qs
except ImportError:
    from cgi import parse_qs

from pyoauth.unicode import to_utf8

def url_equals(url1, url2):
    """
    Compares two URLs and determines whether they are the equal.

    :param url1:
        First URL.
    :param url2:
        Second URL.
    :returns:
        ``True`` if equal; ``False`` otherwise.

    Usage::

        >>> url_equals("http://www.google.com/a", "http://www.google.com/a")
        True
        >>> url_equals("https://www.google.com/a", "http://www.google.com/a")
        False
        >>> url_equals("http://www.google.com/", "http://www.example.com/")
        False
        >>> url_equals("http://example.com:80/", "http://example.com:8000/")
        False
        >>> url_equals("http://user@example.com/", "http://user2@example.com.com/")
        False
        >>> url_equals("http://user@example.com/request?a=b&b=c&b=d#fragment", "http://user@example.com/request?b=c&b=d&a=b#fragment")
        True
        >>> url_equals("http://user@example.com/request?a=b&b=c&b=d#fragment", "http://user@example.com/request?b=c&b=d&a=b#fragment2")
        False
        >>> url_equals("http://www.google.com/request?a=b", "http://www.google.com/request?b=c")
        False
    """
    u1 = urlparse.urlparse(url1)
    u2 = urlparse.urlparse(url2)
    return u1.scheme == u2.scheme and \
        u1.path == u2.path and \
        u1.netloc == u2.netloc and \
        u1.fragment == u2.fragment and \
        parse_qs(u1.query, keep_blank_values=True) == parse_qs(u2.query, keep_blank_values=True)


def url_concat(url, query_params=None):
    """
    Concatenate URL and query parameters regardless of whether
    the URL has existing query parameters.

    :param url:
        The URL to add the query parameters to.
    :param query_params:
        Query parameter dictionary.

    >>> url = url_concat("http://www.example.com/foo?a=b", dict(c="d"))
    >>> url_equals("http://www.example.com/foo?a=b&c=d", url)
    True

    >>> url = url_concat("http://www.example.com/", dict(c="d"))
    >>> url_equals("http://www.example.com/?c=d", url)
    True

    >>> url = url_concat("http://www.example.com/", dict(c="d"))
    >>> url_equals("http://www.example.com/?c=d", url)
    True

    >>> url = url_concat("http://www.example.com/foo?a=b", dict(a="d"))
    >>> url_equals("http://www.example.com/foo?a=b&a=d", url)
    True
    """
    if not query_params:
        return url
    if url[-1] not in ("?", "&"):
        url += "&" if ("?" in url) else "?"
    return url + urllib.urlencode(query_params)


def oauth_generate_nonce():
    """
    Calculates an OAuth nonce.

    :returns:
        A string representation of a randomly-generated hexadecimal OAuth nonce
        as follows::

            Nonce and Timestamp (http://tools.ietf.org/html/rfc5849#section-3.3)
            --------------------------------------------------------------------
            A nonce is a random string, uniquely generated by the client to allow
            the server to verify that a request has never been made before and
            helps prevent replay attacks when requests are made over a non-secure
            channel.  The nonce value MUST be unique across all requests with the
            same timestamp, client credentials, and token combinations.

    """
    return binascii.b2a_hex(uuid.uuid4().bytes)


def oauth_generate_verification_code(length=8):
    """
    Calculates an OAuth verification code.

    The verification code will be displayed by the server if a callback URL
    is not provided by the client. The resource owner (the end-user) may
    need to enter this verification code on a limited device. Therefore,
    we limit the length of this code to 8 characters to keep it suitable
    for manual entry.

    :param length:
        Length of the verification code. Defaults to 8.
    :returns:
        A string representation of a randomly-generated hexadecimal OAuth
        verification code.
    """
    return oauth_generate_nonce()[:length]


def oauth_generate_timestamp():
    """
    Generates an OAuth timestamp.

    :returns:
        A string containing a positive integer representing time as follows::

            Nonce and Timestamp (http://tools.ietf.org/html/rfc5849#section-3.3)
            --------------------------------------------------------------------
            The timestamp value MUST be a positive integer.  Unless otherwise
            specified by the server's documentation, the timestamp is expressed
            in the number of seconds since January 1, 1970 00:00:00 GMT.

    """
    return str(int(time.time()))


def oauth_escape(val):
    """
    Escapes the value of a query string parameter according to the OAuth spec.

    Use only in constructing the signature base string and the "Authorization"
    header field.

    :param val:
        Query string parameter value to escape.
    :returns:
        String representing escaped value as follows::

            Percent Encoding (http://tools.ietf.org/html/rfc5849#section-3.6)
            -----------------------------------------------------------------
            Existing percent-encoding methods do not guarantee a consistent
            construction of the signature base string.  The following percent-
            encoding method is not defined to replace the existing encoding
            methods defined by [RFC3986] and [W3C.REC-html40-19980424].  It is
            used only in the construction of the signature base string and the
            "Authorization" header field.

            This specification defines the following method for percent-encoding
            strings:

            1.  Text values are first encoded as UTF-8 octets per [RFC3629] if
               they are not already.  This does not include binary values that
               are not intended for human consumption.

            2.  The values are then escaped using the [RFC3986] percent-encoding
               (%XX) mechanism as follows:

               *  Characters in the unreserved character set as defined by
                  [RFC3986], Section 2.3 (ALPHA, DIGIT, "-", ".", "_", "~") MUST
                  NOT be encoded.

               *  All other characters MUST be encoded.

               *  The two hexadecimal characters used to represent encoded
                  characters MUST be uppercase.

            This method is different from the encoding scheme used by the
            "application/x-www-form-urlencoded" content-type (for example, it
            encodes space characters as "%20" and not using the "+" character).
            It MAY be different from the percent-encoding functions provided by
            web-development frameworks (e.g., encode different characters, use
            lowercase hexadecimal characters).

    """
    if isinstance(val, unicode):
        val = val.encode("utf-8")
    return urllib.quote(val, safe="~")


def oauth_get_signature_base_string(url, method, query_params):
    """
    Calculates a signature base string based on the URL, method, and
    query_parameters.

    Any query parameter by the name "oauth_signature" will be excluded
    from the base string.

    :param url:
        The URL. If this includes a query string, query parameters are first
        extracted and encoded as well. Query parameters in the URL are
        overridden by those found in the ``query_params`` argument to this
        function.
    :param method:
        HTTP request method.
    :param query_params:
        Query string parameters.
    :returns:
        Base string as per rfc5849#section-3.4.1 as follows::

            Signature base string (http://tools.ietf.org/html/rfc5849#section-3.4.1)
            ------------------------------------------------------------------------
            The signature base string is a consistent, reproducible concatenation
            of several of the HTTP request elements into a single string.  The
            string is used as an input to the "HMAC-SHA1" and "RSA-SHA1"
            signature methods.

            The signature base string includes the following components of the
            HTTP request:

            *  The HTTP request method (e.g., "GET", "POST", etc.).

            *  The authority as declared by the HTTP "Host" request header field.

            *  The path and query components of the request resource URI.

            *  The protocol parameters excluding the "oauth_signature".

            *  Parameters included in the request entity-body if they comply with
               the strict restrictions defined in Section 3.4.1.3.

            The signature base string does not cover the entire HTTP request.
            Most notably, it does not include the entity-body in most requests,
            nor does it include most HTTP entity-headers.  It is important to
            note that the server cannot verify the authenticity of the excluded
            request components without using additional protections such as SSL/
            TLS or other methods.

            ...

    Usage::

        >>> base_string = oauth_get_signature_base_string( \
                "http://example.com/request?b5=%3D%253D&a3=a&c%40=&a2=r%20b&c2&a3=2+q", \
                "POST", \
                dict( \
                    oauth_consumer_key="9djdj82h48djs9d2", \
                    oauth_token="kkk9d7dh3k39sjv7", \
                    oauth_signature_method="HMAC-SHA1", \
                    oauth_timestamp="137131201", \
                    oauth_nonce="7d8f3e4a", \
                    oauth_signature="bYT5CMsGcbgUdFHObYMEfcx6bsw%3D"))
        >>> base_string == "POST&http%3A%2F%2Fexample.com%2Frequest&a2%3Dr%2520b%26a3%3D2%2520q%26a3%3Da%26b5%3D%253D%25253D%26c%2540%3D%26c2%3D%26oauth_consumer_key%3D9djdj82h48djs9d2%26oauth_nonce%3D7d8f3e4a%26oauth_signature_method%3DHMAC-SHA1%26oauth_timestamp%3D137131201%26oauth_token%3Dkkk9d7dh3k39sjv7"
        True
    """
    normalized_url, url_query_params = oauth_get_normalized_url_and_query_params(url)
    url_query_params.update(query_params)
    query_string = oauth_get_normalized_query_string(url_query_params)
    return "&".join(oauth_escape(e) for e in [method.upper(), normalized_url, query_string])


def oauth_get_normalized_query_string(query_params):
    """
    Normalizes a dictionary of query parameters according to OAuth spec.

    :param query_params:
        Query string parameters. A query parameter by the name
        "oauth_signature" or "OAuth realm", if present, will be excluded
        from the query string.
    :returns:
        Normalized string of query parameters as follows::

            Parameter Normalization (http://tools.ietf.org/html/rfc5849#section-3.4.1.3.2)
            ------------------------------------------------------------------------------
            The parameters collected in Section 3.4.1.3 are normalized into a
            single string as follows:

            1.  First, the name and value of each parameter are encoded
               (Section 3.6).

            2.  The parameters are sorted by name, using ascending byte value
               ordering.  If two or more parameters share the same name, they
               are sorted by their value.

            3.  The name of each parameter is concatenated to its corresponding
               value using an "=" character (ASCII code 61) as a separator, even
               if the value is empty.

            4.  The sorted name/value pairs are concatenated together into a
               single string by using an "&" character (ASCII code 38) as
               separator.

            For example, the list of parameters from the previous section would
            be normalized as follows:

                                         Encoded:

                       +------------------------+------------------+
                       |          Name          |       Value      |
                       +------------------------+------------------+
                       |           b5           |     %3D%253D     |
                       |           a3           |         a        |
                       |          c%40          |                  |
                       |           a2           |       r%20b      |
                       |   oauth_consumer_key   | 9djdj82h48djs9d2 |
                       |       oauth_token      | kkk9d7dh3k39sjv7 |
                       | oauth_signature_method |     HMAC-SHA1    |
                       |     oauth_timestamp    |     137131201    |
                       |       oauth_nonce      |     7d8f3e4a     |
                       |           c2           |                  |
                       |           a3           |       2%20q      |
                       +------------------------+------------------+

                                          Sorted:

                       +------------------------+------------------+
                       |          Name          |       Value      |
                       +------------------------+------------------+
                       |           a2           |       r%20b      |
                       |           a3           |       2%20q      |
                       |           a3           |         a        |
                       |           b5           |     %3D%253D     |
                       |          c%40          |                  |
                       |           c2           |                  |
                       |   oauth_consumer_key   | 9djdj82h48djs9d2 |
                       |       oauth_nonce      |     7d8f3e4a     |
                       | oauth_signature_method |     HMAC-SHA1    |
                       |     oauth_timestamp    |     137131201    |
                       |       oauth_token      | kkk9d7dh3k39sjv7 |
                       +------------------------+------------------+

                                    Concatenated Pairs:

                          +-------------------------------------+
                          |              Name=Value             |
                          +-------------------------------------+
                          |               a2=r%20b              |
                          |               a3=2%20q              |
                          |                 a3=a                |
                          |             b5=%3D%253D             |
                          |                c%40=                |
                          |                 c2=                 |
                          | oauth_consumer_key=9djdj82h48djs9d2 |
                          |         oauth_nonce=7d8f3e4a        |
                          |   oauth_signature_method=HMAC-SHA1  |
                          |      oauth_timestamp=137131201      |
                          |     oauth_token=kkk9d7dh3k39sjv7    |
                          +-------------------------------------+

            and concatenated together into a single string (line breaks are for
            display purposes only)::

                 a2=r%20b&a3=2%20q&a3=a&b5=%3D%253D&c%40=&c2=&oauth_consumer_key=9dj
                 dj82h48djs9d2&oauth_nonce=7d8f3e4a&oauth_signature_method=HMAC-SHA1
                 &oauth_timestamp=137131201&oauth_token=kkk9d7dh3k39sjv7

    Usage::

        >>> qs = oauth_get_normalized_query_string({ \
                'b5': ['=%3D'], \
                'a3': ['a', '2 q'], \
                'c@': [''], \
                'a2': ['r b'], \
                'oauth_consumer_key': '9djdj82h48djs9d2', \
                'oauth_token': 'kkk9d7dh3k39sjv7', \
                'oauth_signature_method': 'HMAC-SHA1', \
                'oauth_timestamp': '137131201', \
                'oauth_nonce': '7d8f3e4a', \
                'c2': [''], \
            })
        >>> assert qs == "a2=r%20b&a3=2%20q&a3=a&b5=%3D%253D&c%40=&c2=&oauth_consumer_key=9djdj82h48djs9d2&oauth_nonce=7d8f3e4a&oauth_signature_method=HMAC-SHA1&oauth_timestamp=137131201&oauth_token=kkk9d7dh3k39sjv7"

        >>> # Do not UTF-8 encode byte strings. Only Unicode strings should be UTF-8 encoded.
        >>> bytestring = '\x1d\t\xa8\x93\xf9\xc9A\xed\xae\x08\x18\xf5\xe8W\xbd\xd5'
        >>> q = oauth_get_normalized_query_string(dict(bytestring=bytestring))
        >>> parse_qs('bytestring=%1D%09%A8%93%F9%C9A%ED%AE%08%18%F5%E8W%BD%D5')['bytestring'][0] == bytestring
        True
    """
    if not query_params:
        return ""
    encoded_pairs = []
    for k, v in query_params.iteritems():
        # Keys are also percent-encoded according to OAuth spec.
        k = oauth_escape(to_utf8(k))
        if k == "oauth_signature" or k == "OAuth realm":
            continue
        elif isinstance(v, basestring):
            encoded_pairs.append((k, oauth_escape(v),))
        else:
            try:
                v = list(v)
            except TypeError, e:
                assert "is not iterable" in str(e)
                encoded_pairs.append((k, oauth_escape(str(v)), ))
            else:
                for i in v:
                    if isinstance(i, basestring):
                        encoded_pairs.append((k, oauth_escape(i), ))
                    else:
                        encoded_pairs.append((k, oauth_escape(str(i)), ))
    query_string = "&".join(["%s=%s" % (k, v) for k, v in sorted(encoded_pairs)])
    return query_string


def oauth_get_normalized_url_and_query_params(url):
    """
    Normalizes a URL that will be used in the oauth signature and parses
    query parameters as well.

    :param url:
        The URL to normalize.
    :returns:
        Tuple as (normalized URL, query parameters dictionary) as follows::

            Parameter Sources (http://tools.ietf.org/html/rfc5849#section-3.4.1.3.1)
            ------------------------------------------------------------------------
            The parameters from the following sources are collected into a single
            list of name/value pairs:

            o  The query component of the HTTP request URI as defined by
              [RFC3986], Section 3.4.  The query component is parsed into a list
              of name/value pairs by treating it as an
              "application/x-www-form-urlencoded" string, separating the names
              and values and decoding them as defined by
              [W3C.REC-html40-19980424], Section 17.13.4.

            o  The OAuth HTTP "Authorization" header field (Section 3.5.1) if
              present.  The header's content is parsed into a list of name/value
              pairs excluding the "realm" parameter if present.  The parameter
              values are decoded as defined by Section 3.5.1.

            o  The HTTP request entity-body, but only if all of the following
              conditions are met:

              *  The entity-body is single-part.

              *  The entity-body follows the encoding requirements of the
                 "application/x-www-form-urlencoded" content-type as defined by
                 [W3C.REC-html40-19980424].

              *  The HTTP request entity-header includes the "Content-Type"
                 header field set to "application/x-www-form-urlencoded".

              The entity-body is parsed into a list of decoded name/value pairs
              as described in [W3C.REC-html40-19980424], Section 17.13.4.

            The "oauth_signature" parameter MUST be excluded from the signature
            base string if present.  Parameters not explicitly included in the
            request MUST be excluded from the signature base string (e.g., the
            "oauth_version" parameter when omitted).

            For example, the HTTP request:

               POST /request?b5=%3D%253D&a3=a&c%40=&a2=r%20b HTTP/1.1
               Host: example.com
               Content-Type: application/x-www-form-urlencoded
               Authorization: OAuth realm="Example",
                              oauth_consumer_key="9djdj82h48djs9d2",
                              oauth_token="kkk9d7dh3k39sjv7",
                              oauth_signature_method="HMAC-SHA1",
                              oauth_timestamp="137131201",
                              oauth_nonce="7d8f3e4a",
                              oauth_signature="djosJKDKJSD8743243%2Fjdk33klY%3D"

               c2&a3=2+q

            contains the following (fully decoded) parameters used in the
            signature base sting:

                       +------------------------+------------------+
                       |          Name          |       Value      |
                       +------------------------+------------------+
                       |           b5           |       =%3D       |
                       |           a3           |         a        |
                       |           c@           |                  |
                       |           a2           |        r b       |
                       |   oauth_consumer_key   | 9djdj82h48djs9d2 |
                       |       oauth_token      | kkk9d7dh3k39sjv7 |
                       | oauth_signature_method |     HMAC-SHA1    |
                       |     oauth_timestamp    |     137131201    |
                       |       oauth_nonce      |     7d8f3e4a     |
                       |           c2           |                  |
                       |           a3           |        2 q       |
                       +------------------------+------------------+

            Note that the value of "b5" is "=%3D" and not "==".  Both "c@" and
            "c2" have empty values.  While the encoding rules specified in this
            specification for the purpose of constructing the signature base
            string exclude the use of a "+" character (ASCII code 43) to
            represent an encoded space character (ASCII code 32), this practice
            is widely used in "application/x-www-form-urlencoded" encoded values,
            and MUST be properly decoded, as demonstrated by one of the "a3"
            parameter instances (the "a3" parameter is used twice in this
            request).

    Usage::

        >>> u, q = oauth_get_normalized_url_and_query_params("HTTP://eXample.com/request?b5=%3D%253D&a3=a&c%40=&a2=r%20b")
        >>> assert u == "http://example.com/request"
        >>> assert q == {'a2': ['r b'], 'a3': ['a'], 'b5': ['=%3D'], 'c@': ['']}
        >>> u, q = oauth_get_normalized_url_and_query_params("http://example.com/request?c2&a3=2+q")
        >>> assert u == "http://example.com/request"
        >>> assert q == {'a3': ['2 q'], 'c2': ['']}
        >>> u, q = oauth_get_normalized_url_and_query_params("HTTP://eXample.com/request?b5=%3D%253D&a3=a&c%40=&a2=r%20b&c2&a3=2+q")
        >>> assert u == "http://example.com/request"
        >>> assert q == {'a2': ['r b'], 'a3': ['a', '2 q'], 'b5': ['=%3D'], 'c@': [''], 'c2': ['']}

    """
    parts = urlparse.urlparse(url)
    scheme, netloc, path, _, query_string = parts[:5]
    normalized_url = scheme.lower() + "://" + netloc.lower() + path
    query_params = oauth_parse_qs(query_string)
    return normalized_url, query_params


def oauth_parse_qs(qs):
    """
    Parses a query parameter string according to the OAuth spec.

    See Parameter Sources (http://tools.ietf.org/html/rfc5849#section-3.4.1.3.1)

    Usage::

        >>> qs = 'b5=%3D%253D&a3=a&c%40=&a2=r%20b' + '&' + 'c2&a3=2+q'
        >>> q = oauth_parse_qs(qs)
        >>> assert q == {'a2': ['r b'], 'a3': ['a', '2 q'], 'b5': ['=%3D'], 'c@': [''], 'c2': ['']}
    """
    return parse_qs(qs.encode("utf-8"), keep_blank_values=True)


def oauth_hmac_sha1_signature(consumer_secret, method, url, query_params=None, token_secret=None):
    """
    Calculates an HMAC-SHA1 signature for a base string.

    :param consumer_secret:
        Consumer secret
    :param method:
        Base string HTTP method.
    :param url:
        Base string URL.
    :param query_params:
        Base string query parameters.
    :param token_secret:
        Token secret if available.
    :returns:
        Signature as follows::

            HMAC-SHA1 (http://tools.ietf.org/html/rfc5849#section-3.4.2)
            ------------------------------------------------------------
            The "HMAC-SHA1" signature method uses the HMAC-SHA1 signature
            algorithm as defined in [RFC2104]:

             digest = HMAC-SHA1 (key, text)

            The HMAC-SHA1 function variables are used in following way:

            text    is set to the value of the signature base string from
                   Section 3.4.1.1.

            key     is set to the concatenated values of:

                   1.  The client shared-secret, after being encoded
                       (Section 3.6).

                   2.  An "&" character (ASCII code 38), which MUST be included
                       even when either secret is empty.

                   3.  The token shared-secret, after being encoded
                       (Section 3.6).

            digest  is used to set the value of the "oauth_signature" protocol
                   parameter, after the result octet string is base64-encoded
                   per [RFC2045], Section 6.8.
    """
    query_params = query_params or {}
    base_string = oauth_get_signature_base_string(url, method, query_params)

    key_elems = [oauth_escape(consumer_secret)]
    key_elems.append(oauth_escape(token_secret) if token_secret else "")
    key = "&".join(key_elems)

    hashed = hmac.new(key, base_string, hashlib.sha1)
    return binascii.b2a_base64(hashed.digest())[:-1]


def oauth_plaintext_signature(consumer_secret, method, url, query_params=None, token_secret=None):
    """
    Calculates a PLAINTEXT signature for a base string.

    :param consumer_secret:
        Consumer secret
    :param method:
        Base string HTTP method.
    :param url:
        Base string URL.
    :param query_params:
        Base string query parameters.
    :param token_secret:
        Token secret if available.
    :returns:
        Signature as follows::

            PLAINTEXT (http://tools.ietf.org/html/rfc5849#section-3.4.4)
            ------------------------------------------------------------
            The "PLAINTEXT" method does not employ a signature algorithm.  It
            MUST be used with a transport-layer mechanism such as TLS or SSL (or
            sent over a secure channel with equivalent protections).  It does not
            utilize the signature base string or the "oauth_timestamp" and
            "oauth_nonce" parameters.

            The "oauth_signature" protocol parameter is set to the concatenated
            value of:

            1.  The client shared-secret, after being encoded (Section 3.6).

            2.  An "&" character (ASCII code 38), which MUST be included even
               when either secret is empty.

            3.  The token shared-secret, after being encoded (Section 3.6).
    """
    sig_elems = [oauth_escape(consumer_secret)]
    sig_elems.append(oauth_escape(token_secret) if token_secret else "")
    return "&".join(sig_elems)
