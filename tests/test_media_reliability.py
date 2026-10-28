"""Media API reliability regression tests without network access."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import image as media  # noqa: E402


def test_multipart_upload_uses_streaming_encoder():
    old_post = media.requests.post
    old_key = media.API_KEY
    captured = {}

    class Response:
        ok = True

        @staticmethod
        def json():
            return {'id': 'file-streamed'}

    def post(url, **kwargs):
        captured.update(kwargs)
        assert isinstance(kwargs['data'], media.MultipartEncoder)
        assert kwargs['data'].len > 1024
        return Response()

    try:
        media.API_KEY = 'test-only-key'
        media.requests.post = post
        with tempfile.NamedTemporaryFile(suffix='.mp4') as video:
            video.write(b'x' * 2048)
            video.flush()
            result = media._http_post_multipart(
                'https://example.test/files',
                {'purpose': 'user_data'},
                file_field='file',
                file_path=video.name,
            )
        assert result == {'id': 'file-streamed'}
        assert captured['headers']['Content-Type'].startswith('multipart/form-data; boundary=')
    finally:
        media.requests.post = old_post
        media.API_KEY = old_key


def test_review_parse_failure_is_not_pass():
    old_post = media._http_post_json
    try:
        media._http_post_json = lambda *args, **kwargs: {
            'output': [{'type': 'message', 'content': [
                {'type': 'output_text', 'text': 'not-json'}
            ]}]
        }
        with tempfile.NamedTemporaryFile(suffix='.jpg') as image:
            image.write(b'fake-image')
            image.flush()
            result = media.review_image(image.name)
        assert result['verdict'] == 'ERROR'
        assert result['issues']
    finally:
        media._http_post_json = old_post


def test_video_detection_does_not_match_plain_word_video():
    assert media._is_video_media('/tmp/video_notes.jpg') is False
    assert media._is_video_media('https://example.test/assets/video-cover.webp') is False
    assert media._is_video_media('https://example.test/media/clip.mp4?token=x') is True


def test_image_preparation_failure_uses_media_specific_message():
    original = media.image_to_url
    try:
        media.image_to_url = lambda value: (_ for _ in ()).throw(
            ValueError("unreadable image"))
        result = media.analyze_image("/tmp/broken.jpg", "inspect")
    finally:
        media.image_to_url = original

    assert result.startswith("[Error] 图片准备失败")
    assert "视频预处理" not in result


def test_upload_poll_failure_deletes_remote_file():
    old_post = media._http_post_multipart
    old_get = media._http_get_json
    old_delete = media.delete_uploaded_file
    deleted = []
    try:
        media._http_post_multipart = lambda *args, **kwargs: {
            'id': 'file-failed',
            'status': 'processing',
        }
        media._http_get_json = lambda *args, **kwargs: {'status': 'failed', 'error': 'bad video'}
        media.delete_uploaded_file = lambda file_id: deleted.append(file_id) or True
        try:
            media.upload_video_for_understanding('https://example.test/clip.mp4', poll_timeout=1)
            assert False, 'expected preprocessing failure'
        except RuntimeError:
            pass
        assert deleted == ['file-failed']
    finally:
        media._http_post_multipart = old_post
        media._http_get_json = old_get
        media.delete_uploaded_file = old_delete


def test_partial_video_preparation_is_marked_incomplete():
    old_prepare = media._prepare_one_video
    old_post = media._http_post_json
    try:
        def prepare(value):
            if 'bad' in value:
                return {
                    'file_id': None,
                    'video_url': None,
                    'uploaded_fid': None,
                    'temp_file': None,
                    'error': 'cannot load bad video',
                }
            return {
                'file_id': 'file-external',
                'video_url': None,
                'uploaded_fid': None,
                'temp_file': None,
                'error': None,
            }

        media._prepare_one_video = prepare
        media._http_post_json = lambda *args, **kwargs: {
            'output': [{'type': 'message', 'content': [
                {'type': 'output_text', 'text': 'analysis'}
            ]}]
        }
        result = media.analyze_image(
            ['https://example.test/good.mp4', 'https://example.test/bad.mp4'],
            'analyze',
        )
        assert result.startswith('【⚠️未完成·不可作为全量结论】')
        assert 'cannot load bad video' in result
        assert result.endswith('analysis')
    finally:
        media._prepare_one_video = old_prepare
        media._http_post_json = old_post


def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for test in tests:
        test()
        print(f'  PASS  {test.__name__}')
    print(f'\n{len(tests)} passed, 0 failed (total {len(tests)})')


if __name__ == '__main__':
    _main()
