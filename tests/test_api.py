import json
from unittest import TestCase

from mock import MagicMock
from mock import Mock
from mock import patch
from requests import Response
from requests.exceptions import HTTPError

from chirpy import Twitter, TwitterHTTPError


class TwitterApiTests(TestCase):

    @patch('chirpy.api.requests.post')
    def test_update_with_media(self, http_post):
        oauth = Mock()
        image = "image bytes go here"
        new_post_id = '123455-6778'
        post_resp_data = json.dumps(dict(id_str=new_post_id))
        media_upload_resp = MagicMock(spec=Response, status_code=200)
        media_upload_resp.json.return_value = dict(media_id=3)
        status_update_resp = Mock(spec=Response, headers={'Content-Type': 'application/json'}, content=post_resp_data, status_code=200)

        http_post.side_effect = [
            media_upload_resp,
            status_update_resp,
        ]

        tw = Twitter(auth=oauth)

        resp = tw.update_with_media("this is a tweet msg", image, _timeout=6)

        self.assertTrue('id_str' in resp)
        self.assertEqual(resp['id_str'], new_post_id)

    @patch('chirpy.api.requests.post')
    def test_update_with_media_media_upload_failure(self, http_post):
        oauth = Mock()
        image = "image bytes"
        media_upload_resp = Mock(spec=Response, status_code=400, content='failure failboat')
        media_upload_resp.raise_for_status.side_effect = HTTPError('400 Client Error: failure failboat', response=media_upload_resp)

        http_post.return_value = media_upload_resp  # HTTPError('%s Client Error: %s' % (400, 'Ish Broke'))

        tw = Twitter(auth=oauth)

        with self.assertRaises(TwitterHTTPError):
            tw.update_with_media("this is a tweet text", image, _timeout=7)

    @patch('chirpy.api.requests.post')
    def test_update_with_media_post_failure(self, http_post):
        oauth = Mock()
        image = "image bytes"
        media_upload_resp = MagicMock(spec=Response, status_code=200)
        media_upload_resp.json.return_value = dict(media_id=3)
        status_update_resp = Mock(spec=Response, status_code=400, content='failure failboat')
        status_update_resp.raise_for_status.side_effect = HTTPError('400 Client Error: failure failboat', response=media_upload_resp)

        http_post.side_effect = [
            media_upload_resp,
            status_update_resp
        ]

        tw = Twitter(auth=oauth)

        with self.assertRaises(TwitterHTTPError):
            tw.update_with_media("this is a tweet text", image, _timeout=7)


    def test_update_with_media_media_is_none(self):

        oauth = Mock()
        tw = Twitter(auth=oauth)
        