import importlib.util
from pathlib import Path
import tempfile
import unittest

import app
import tmdb_covers

spec=importlib.util.spec_from_file_location('discovery',Path(__file__).resolve().parents[1]/'scripts'/'descobrir_tv.py')
discovery=importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)

class CoreTests(unittest.TestCase):
    def setUp(self):
        scratch=Path(__file__).resolve().parents[1]/'work'
        scratch.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=scratch)
        self.original=(app.DATA,app.DB)
        app.DATA=Path(self.temp.name)
        (app.DATA/'capas').mkdir()
        app.DB=app.DATA/'test.sqlite'
        app.initialize()
        app.STATE['active']=None
        app.MISSING_FILES.clear()
    def tearDown(self):
        app.DATA,app.DB=self.original
        self.temp.cleanup()
    def media(self,ident,path):
        with app.connect() as db:
            db.execute('INSERT INTO media(id,path,title,duration,position) VALUES (?,?,?,?,?)',(ident,str(path),'Teste',1000,60))
    def test_stop_does_not_erase_progress(self):
        self.media('a'*20,app.DATA/'test.mp4')
        app.save_position('a'*20,0,1000)
        self.assertEqual(app.get_media('a'*20)['position'],60)
        app.save_position('a'*20,100,1000)
        self.assertEqual(app.get_media('a'*20)['position'],100)
    def test_remove_preserves_video_and_deletes_cover(self):
        video=app.DATA/'test.mp4'
        video.write_bytes(b'test')
        self.media('a'*20,video)
        cover=app.DATA/'capas'/('a'*20+'.tmdb.jpg')
        cover.write_bytes(b'test')
        app.remove_media('a'*20)
        self.assertTrue(video.exists())
        self.assertFalse(cover.exists())
    def test_missing_video_requires_confirmation(self):
        self.media('a'*20,app.DATA/'gone.mp4')
        app.sync_library(now=10)
        self.assertEqual(app.get_media('a'*20)['position'],60)
        app.sync_library(now=21)
        with self.assertRaises(ValueError): app.get_media('a'*20)
    def test_unavailable_drive_keeps_bookmark(self):
        self.media('a'*20,app.DATA/'offline.mp4')
        previous=app.video_file_state
        try:
            app.video_file_state=lambda path:'unavailable'
            app.sync_library(now=10)
            app.sync_library(now=30)
            self.assertEqual(app.get_media('a'*20)['position'],60)
        finally: app.video_file_state=previous
    def test_episode_grouping(self):
        self.assertEqual(app.episode_info(Path('My.Show.S02E10.mkv')),('My Show',2,10))
        self.assertEqual(app.episode_info(Path('My.Show.1x02.mp4')),('My Show',1,2))
    def test_ambiguous_cover_not_automatically_selected(self):
        self.assertIsNone(tmdb_covers.automatic_choice([{'score':1},{'score':.99}]))
    def test_description_control_url(self):
        xml=b'''<root xmlns="urn:schemas-upnp-org:device-1-0"><URLBase>http://192.168.1.20:1234/</URLBase><device><friendlyName>Test TV</friendlyName><serviceList><service><serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType><controlURL>/control/av</controlURL></service></serviceList></device></root>'''
        item=discovery.parse_description(xml,'http://192.168.1.20:1234/device.xml')[0]
        self.assertEqual(item['control_url'],'http://192.168.1.20:1234/control/av')
        self.assertEqual(item['control_port'],1234)

if __name__=='__main__': unittest.main()
