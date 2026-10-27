import io
import unittest
from pathlib import Path

from app.web_app import create_app


class UploadApiTests(unittest.TestCase):
    def test_upload_returns_saved_file(self):
        app = create_app()
        app.config.update(TESTING=True)
        with app.test_client() as client:
            response = client.post('/api/upload', data={
                'files': (io.BytesIO('公司名称\n测试有限公司\n'.encode()), 'upload-test.csv')
            }, content_type='multipart/form-data')
        Path('data/inbox/upload-test.csv').unlink(missing_ok=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['ok'])
        self.assertEqual(response.json['saved'], ['upload-test.csv'])

    def test_upload_reports_unsupported_extension(self):
        app = create_app()
        app.config.update(TESTING=True)
        with app.test_client() as client:
            response = client.post('/api/upload', data={
                'files': (io.BytesIO(b'hello'), 'upload-test.exe')
            }, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json['ok'])
        self.assertIn('不支持的格式', response.json['skipped'][0])

    def test_upload_reports_size_limit_as_json(self):
        app = create_app()
        app.config.update(TESTING=True, MAX_CONTENT_LENGTH=100)
        with app.test_client() as client:
            response = client.post('/api/upload', data={
                'files': (io.BytesIO(b'x' * 101), 'large.csv')
            }, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 413)
        self.assertTrue(response.is_json)
        self.assertFalse(response.json['ok'])
        self.assertIn('文件过大', response.json['error'])


if __name__ == '__main__':
    unittest.main()
