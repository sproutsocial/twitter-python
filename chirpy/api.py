import re
import requests
import ssl

from urllib3.util.ssl_ import create_urllib3_context

from functools import partial

try:
    from cStringIO import StringIO
except ImportError:
    from io import BytesIO as StringIO

try:
    import json
except ImportError:
    import simplejson as json


from .twitter_globals import POST_ACTIONS
from .auth import NoAuth


class _DEFAULT(object):
    pass

# fix for a sudden twitter outage on 8/4/2023
class TwitterNoSSLSessionTicketExtension(requests.adapters.HTTPAdapter):
    def __init__(self):
        self.ssl_context = create_urllib3_context()
        self.ssl_context.options &= ~ssl.OP_NO_TICKET
        super().__init__()

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self.ssl_context
        super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs["ssl_context"] = self.ssl_context
        return super().proxy_manager_for(*args, **kwargs)

class TwitterError(Exception):
    """
    Base Exception thrown by the Twitter object when there is a
    general error interacting with the API.
    """
    pass

class TwitterHTTPError(TwitterError):
    """
    Exception thrown by the Twitter object when there is an
    HTTP error interacting with twitter.com.
    """
    def __init__(self, e, uri, format, uriparts):
        self.e = e
        self.uri = uri
        self.format = format
        self.uriparts = uriparts
        self.response_data = self.e.response.content

    def __str__(self):
        fmt = ("." + self.format) if self.format else ""
        return (
            "Twitter sent status %i for URL: %s%s using parameters: "
            "(%s)\ndetails: %s" % (
                self.e.response.status_code, self.uri, fmt, self.uriparts,
                self.response_data))

class TwitterResponse(object):
    """
    Response from a twitter request. Behaves like a list or a string
    (depending on requested format) but it has a few other interesting
    attributes.

    `headers` gives you access to the response headers as an
    httplib.HTTPHeaders instance. You can do
    `response.headers.get('h')` to retrieve a header.
    """
    def __init__(self, headers):
        self.headers = headers

    @property
    def rate_limit_remaining(self):
        """
        Remaining requests in the current rate-limit.
        """
        return int(self.headers.get('X-Rate-Limit-Remaining', "0"))

    @property
    def rate_limit_limit(self):
        """
        The rate limit ceiling for that given request.
        """
        return int(self.headers.get('X-Rate-Limit-Limit', "0"))

    @property
    def rate_limit_reset(self):
        """
        Time in UTC epoch seconds when the rate limit will reset.
        """
        return int(self.headers.get('X-Rate-Limit-Reset', "0"))


def wrap_response(response, headers):
    response_typ = type(response)
    if response_typ is bool:
        # HURF DURF MY NAME IS PYTHON AND I CAN'T SUBCLASS bool.
        response_typ = int

    class WrappedTwitterResponse(response_typ, TwitterResponse):
        __doc__ = TwitterResponse.__doc__

        def __init__(self, response, headers):
            response_typ.__init__(self, response)
            TwitterResponse.__init__(self, headers)
        def __new__(cls, response, headers):
            return response_typ.__new__(cls, response)


    return WrappedTwitterResponse(response, headers)



class TwitterCall(object):

    def __init__(self, auth, format, domain, callable_cls, uri="",
            uriparts=None, secure=True, headers=None, proxies=None,
            default_timeout=None):
        self.auth = auth
        self.format = format
        self.domain = domain
        self.callable_cls = callable_cls
        self.uri = uri
        self.uriparts = uriparts
        self.secure = secure
        self.headers = headers or {}
        self.proxies = proxies or {}
        self.default_timeout = default_timeout
        self.is_post = False

    def __getattr__(self, k):
        try:
            return object.__getattr__(self, k)
        except AttributeError:
            def extend_call(arg):
                return self.callable_cls(
                    auth=self.auth, format=self.format, domain=self.domain,
                    callable_cls=self.callable_cls, uriparts=self.uriparts \
                        + (arg,),
                    secure=self.secure, headers=self.headers,
                    proxies=self.proxies,
                    default_timeout=self.default_timeout)
            if k == "_":
                return extend_call
            else:
                return extend_call(k)

    def __call__(self, **kwargs):
        # Build the uri.
        uriparts = []
        for uripart in self.uriparts:
            # If this part matches a keyword argument, use the
            # supplied value otherwise, just use the part.
            uriparts.append(str(kwargs.pop(uripart, uripart)))
        uri = '/'.join(uriparts)

        json_body = kwargs.pop('_json', None)

        method = kwargs.pop('_method', None)
        if not method:
            method = "GET"

            if json_body is not None:
                method = "POST"

            for action in POST_ACTIONS:
                if re.search("%s(/\d+)?$" % action, uri):
                    method = "POST"
                    break

        # If an id kwarg is present and there is no id to fill in in
        # the list of uriparts, assume the id goes at the end.
        id = kwargs.pop('id', None)
        if id:
            uri += "/%s" % (id)

        # If an _id kwarg is present, this is treated as id as a CGI
        # param.
        _id = kwargs.pop('_id', None)
        if _id:
            kwargs['id'] = _id

        # If an _timeout is specified in kwargs, use it
        _timeout = kwargs.pop('_timeout', self.default_timeout)

        secure_str = ''
        if self.secure:
            secure_str = 's'
        dot = ""
        if self.format:
            dot = "."
        uriBase = "http%s://%s/%s%s%s" % (
                    secure_str, self.domain, uri, dot, self.format)

        headers = {'Accept-Encoding': 'gzip'}
        headers.update(self.headers)

        adapter = TwitterNoSSLSessionTicketExtension()
        req_session = requests.Session()
        req_session.mount('https://api.twitter.com', adapter)
        req_session.mount('http://api.twitter.com', adapter)

        if json_body is not None:
            headers['Content-Type'] = 'application/json; charset=utf-8'

        if method == 'GET' or method == 'DELETE':
            request = partial(req_session.request, params=kwargs)
        elif method == 'POST':
            post_data = json.dumps(json_body) if json_body is not None else kwargs
            request = partial(req_session.request, data=post_data)
        elif method == 'PUT':
            put_data = json.dumps(json_body) if json_body is not None else kwargs
            request = partial(req_session.request, params=kwargs, data=put_data)

        resp = request(method, uriBase, headers=headers, timeout=_timeout,
            proxies=self.proxies, auth=self.auth)

        try:
            return self._handle_response(resp)
        except requests.exceptions.HTTPError as e:
            raise TwitterHTTPError(e, uri, self.format, kwargs)

    def _handle_response(self, resp):
        resp.raise_for_status() # if something went wrong, raise an exception

        # If it's no content, return an empty dict for compatibility
        if resp.status_code == 204:
            return wrap_response({}, resp.headers)

        if resp.headers['Content-Type'] in ['image/jpeg', 'image/png']:
            return StringIO(resp.content)

        if "json" == self.format:
            res = json.loads(resp.content)
            return wrap_response(res, resp.headers)
        else:
            return wrap_response(
                resp.content, resp.headers)

    def update_with_media(self, status, media, **kwargs):
        media_url = "https://upload.twitter.com/1.1/media/upload.json"
        url = "https://api.twitter.com/1.1/statuses/update.json"

        files = {'media': media}
        headers = {'Accept-Encoding': 'gzip'}
        headers.update(self.headers)

        # upload media to twitter
        # expect object containing 'media_ids' field if successful
        resp = requests.post(media_url, data=None, files=files, headers=headers, proxies=self.proxies, auth=self.auth)
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            raise TwitterHTTPError(e, media_url, self.format, kwargs)
        media_data = resp.json()

        # send status with media ID to twitter
        kwargs['status'] = status
        if 'media_ids' not in kwargs:
            kwargs['media_ids'] = []
        kwargs['media_ids'].append(media_data['media_id'])
        resp = requests.post(url, data=kwargs, files=None, headers=headers,
            proxies=self.proxies, auth=self.auth)
        try:
            return self._handle_response(resp)
        except requests.exceptions.HTTPError as e:
            raise TwitterHTTPError(e, url, self.format, kwargs)


class Twitter(TwitterCall):
    """
    The minimalist yet fully featured Twitter API class.

    Get RESTful data by accessing members of this class. The result
    is decoded python objects (lists and dicts).

    The Twitter API is documented at:

      http://dev.twitter.com/doc


    Examples::

        t = Twitter(
            auth=OAuth(token, token_key, con_secret, con_secret_key)))

        # Get your "home" timeline
        t.statuses.home_timeline()

        # Get a particular friend's timeline
        t.statuses.friends_timeline(id="billybob")

        # Also supported (but totally weird)
        t.statuses.friends_timeline.billybob()

        # Update your status
        t.statuses.update(
            status="Using @sixohsix's sweet Python Twitter Tools.")

        # Send a direct message
        t.direct_messages.new(
            user="billybob",
            text="I think yer swell!")

        # Get the members of tamtar's list "Things That Are Rad"
        t._("tamtar")._("things-that-are-rad").members()

        # Note how the magic `_` method can be used to insert data
        # into the middle of a call. You can also use replacement:
        t.user.list.members(user="tamtar", list="things-that-are-rad")

        # An *optional* `_timeout` parameter can also be used for API
        # calls which take much more time than normal or twitter stops
        # responding for some reasone
        t.users.lookup(
            screen_name=','.join(A_LIST_OF_100_SCREEN_NAMES), \
            _timeout=1)



    Searching Twitter::

        # Search for the latest tweets about #pycon
        t.search.tweets(q="#pycon")


    Using the data returned
    -----------------------

    Twitter API calls return decoded JSON. This is converted into
    a bunch of Python lists, dicts, ints, and strings. For example::

        x = twitter.statuses.home_timeline()

        # The first 'tweet' in the timeline
        x[0]

        # The screen name of the user who wrote the first 'tweet'
        x[0]['user']['screen_name']


    Getting raw XML data
    --------------------

    If you prefer to get your Twitter data in XML format, pass
    format="xml" to the Twitter object when you instantiate it::

        twitter = Twitter(format="xml")

    The output will not be parsed in any way. It will be a raw string
    of XML.

    """
    def __init__(
            self, format="json",
            domain="api.twitter.com",
            secure=True,
            auth=None,
            api_version=_DEFAULT,
            headers=None,
            proxies=None,
            default_timeout=None,
            _callable_cls=None):
        """
        Create a new twitter API connector.

        Pass an `auth` parameter to use the credentials of a specific
        user. Generally you'll want to pass an `OAuth`
        instance::

            twitter = Twitter(auth=OAuth(
                    token, token_secret, consumer_key, consumer_secret))


        `domain` lets you change the domain you are connecting. By
        default it's `api.twitter.com` but `search.twitter.com` may be
        useful too.

        If `secure` is False you will connect with HTTP instead of
        HTTPS.

        `api_version` is used to set the base uri. By default it's
        '1'. If you are using "search.twitter.com" set this to None.
        """
        if not auth:
            auth = NoAuth()

        if (format not in ("json", "xml", "")):
            raise ValueError("Unknown data format '%s'" % (format))

        if api_version is _DEFAULT:
            if domain == 'api.twitter.com':
                api_version = '1.1'
            else:
                api_version = None

        uriparts = ()
        if api_version:
            uriparts += (str(api_version),)

        TwitterCall.__init__(
            self, auth=auth, format=format, domain=domain,
            callable_cls=_callable_cls or TwitterCall,
            secure=secure, uriparts=uriparts,
            headers=headers, proxies=proxies,
            default_timeout=default_timeout)


__all__ = ["Twitter", "TwitterError", "TwitterHTTPError", "TwitterResponse"]
