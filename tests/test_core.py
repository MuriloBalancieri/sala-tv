import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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
        app.STATE['pending']=False
        app.STATE.pop('preserve_bookmark',None)
        app.MISSING_FILES.clear()
        app.AUDIO_CLEANUP.clear()
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
    def test_hide_keeps_library_file_and_bookmark_after_poll(self):
        video=app.DATA/'test.mp4'
        video.write_bytes(b'test')
        self.media('a'*20,video)
        app.set_watch_status('a'*20,'hide')
        app.save_position('a'*20,80,1000)
        item=app.get_media('a'*20)
        self.assertEqual((item['continue_hidden'],item['position'],item['watched']),(1,80,0))
        self.assertTrue(video.exists())
        app.initialize()
        self.assertEqual(app.get_media('a'*20)['continue_hidden'],1)
    def test_watched_survives_poll_and_can_be_undone(self):
        self.media('a'*20,app.DATA/'test.mp4')
        app.set_watch_status('a'*20,'watched')
        app.save_position('a'*20,80,1000)
        self.assertEqual(app.get_media('a'*20)['watched'],1)
        app.set_watch_status('a'*20,'unwatched')
        item=app.get_media('a'*20)
        self.assertEqual((item['watched'],item['continue_hidden'],item['position']),(0,0,80))
    def converted(self, ident):
        cache=app.DATA/'audio-cache'
        cache.mkdir(exist_ok=True)
        path=cache/f'{ident}-{"e"*24}.mp4'
        path.write_bytes(b'test')
        return path
    def test_marking_watched_discards_converted_audio(self):
        self.media('a'*20,app.DATA/'test.mp4')
        converted=self.converted('a'*20)
        app.set_watch_status('a'*20,'watched')
        self.assertFalse(converted.exists())
        self.assertNotIn('a'*20,app.AUDIO_CLEANUP)
    def test_unwatched_and_hide_keep_converted_audio(self):
        self.media('a'*20,app.DATA/'test.mp4')
        converted=self.converted('a'*20)
        app.set_watch_status('a'*20,'hide')
        app.set_watch_status('a'*20,'unwatched')
        self.assertTrue(converted.exists())
    def test_watched_while_playing_defers_cleanup_until_the_copy_is_free(self):
        self.media('a'*20,app.DATA/'test.mp4')
        converted=self.converted('a'*20)
        app.STATE['active']='a'*20
        app.set_watch_status('a'*20,'watched')
        self.assertTrue(converted.exists())
        self.assertIn('a'*20,app.AUDIO_CLEANUP)
        app.STATE['active']=None
        app.cleanup_converted()
        self.assertFalse(converted.exists())
        self.assertNotIn('a'*20,app.AUDIO_CLEANUP)
    def test_playing_a_watched_title_cancels_pending_cleanup(self):
        video=app.DATA/'test.mp4'
        video.write_bytes(b'test')
        self.media('a'*20,video)
        converted=self.converted('a'*20)
        app.STATE['active']='a'*20
        app.set_watch_status('a'*20,'watched')
        with patch.object(app,'observe'), patch.object(app,'soap',return_value={}), patch.object(app,'pc_ip',return_value='127.0.0.1'), patch.object(app,'prepare_tv_media',return_value=(converted,'movie.mp4')):
            app.play('a'*20,False)
        self.assertTrue(converted.exists())
        self.assertNotIn('a'*20,app.AUDIO_CLEANUP)
    def test_invalid_watch_action_does_not_change_item(self):
        self.media('a'*20,app.DATA/'test.mp4')
        with self.assertRaises(ValueError): app.set_watch_status('a'*20,'delete')
        self.assertEqual(app.get_media('a'*20)['position'],60)
    def test_replay_watched_starts_over_and_restores_tracking(self):
        video=app.DATA/'test.mp4'
        video.write_bytes(b'test')
        self.media('a'*20,video)
        app.set_watch_status('a'*20,'watched')
        with patch.object(app,'observe'), patch.object(app,'soap',return_value={}) as soap, patch.object(app,'pc_ip',return_value='127.0.0.1'), patch.object(app,'prepare_tv_media',return_value=(video,'movie.mp4')):
            app.play('a'*20,True)
        item=app.get_media('a'*20)
        self.assertEqual((item['watched'],item['continue_hidden'],item['position']),(0,0,0))
        self.assertNotIn('Seek',[call.args[0] for call in soap.call_args_list])
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
    def test_seek_time_formats_and_invalid_input(self):
        for value,expected in [('12:30',750),('01:12:30',4350),('0',0),('90:00',5400)]:
            self.assertEqual(app.parse_seek_time(value),expected)
        for value in ('-1','NaN','1:99','01:70:00','12.5',True,None):
            with self.assertRaises(ValueError): app.parse_seek_time(value)
    def seek_fixture(self):
        ident='a'*20
        self.media(ident,app.DATA/'test.mp4')
        app.STATE['active']=ident
        return ident,{'TrackURI':f'http://127.0.0.1:8768/media/{ident}/movie.mp4','RelTime':'00:01:00','TrackDuration':'00:16:40'}
    def test_seek_paused_to_zero_keeps_flags_and_saves_zero(self):
        ident,info=self.seek_fixture()
        app.set_watch_status(ident,'hide')
        with patch.object(app,'soap',side_effect=[info,{'CurrentTransportState':'PAUSED_PLAYBACK'},{},dict(info,RelTime='00:00:00')]) as soap, patch.object(app.time,'sleep'):
            self.assertEqual(app.seek_video(ident,position='00:00'),0)
        self.assertEqual(app.get_media(ident)['position'],0)
        self.assertEqual(app.get_media(ident)['continue_hidden'],1)
        self.assertEqual([call.args[0] for call in soap.call_args_list],['GetPositionInfo','GetTransportInfo','Seek','GetPositionInfo'])
        self.assertEqual(soap.call_args_list[2].args[1]['Target'],'00:00:00')
    def test_seek_relative_uses_live_time_and_clamps_end(self):
        ident,info=self.seek_fixture()
        info['RelTime']='00:16:35'
        with patch.object(app,'soap',side_effect=[info,{'CurrentTransportState':'PLAYING'},{},dict(info,RelTime='00:16:39')]) as soap, patch.object(app.time,'sleep'):
            self.assertEqual(app.seek_video(ident,offset=10),999)
        self.assertEqual(soap.call_args_list[2].args[1]['Target'],'00:16:39')
    def test_seek_relative_clamps_start(self):
        ident,info=self.seek_fixture()
        info['RelTime']='00:00:05'
        with patch.object(app,'soap',side_effect=[info,{'CurrentTransportState':'PLAYING'},{},dict(info,RelTime='00:00:00')]), patch.object(app.time,'sleep'):
            self.assertEqual(app.seek_video(ident,offset=-10),0)
    def test_seek_other_video_is_not_controlled(self):
        ident,info=self.seek_fixture()
        info['TrackURI']='http://elsewhere/movie.mp4'
        with patch.object(app,'soap',return_value=info) as soap:
            with self.assertRaises(ValueError): app.seek_video(ident,position='02:00')
        self.assertEqual(soap.call_count,1)
        self.assertEqual(app.get_media(ident)['position'],60)
    def test_seek_unconfirmed_preserves_bookmark(self):
        ident,info=self.seek_fixture()
        with patch.object(app,'soap',side_effect=[info,{'CurrentTransportState':'PLAYING'},{}]+[info]*6), patch.object(app.time,'sleep'):
            with self.assertRaises(RuntimeError): app.seek_video(ident,position='05:00')
        self.assertEqual(app.get_media(ident)['position'],60)
        self.assertEqual(app.STATE['preserve_bookmark'],ident)
    def test_seek_rejected_by_tv_preserves_bookmark(self):
        ident,info=self.seek_fixture()
        with patch.object(app,'soap',side_effect=[info,{'CurrentTransportState':'PLAYING'},RuntimeError('Unsupported')]):
            with self.assertRaises(RuntimeError): app.seek_video(ident,position='05:00')
        self.assertEqual(app.get_media(ident)['position'],60)
    def test_ambiguous_cover_not_automatically_selected(self):
        self.assertIsNone(tmdb_covers.automatic_choice([{'score':1},{'score':.99}]))
    def test_description_control_url(self):
        xml=b'''<root xmlns="urn:schemas-upnp-org:device-1-0"><URLBase>http://192.168.1.20:1234/</URLBase><device><friendlyName>Test TV</friendlyName><serviceList><service><serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType><controlURL>/control/av</controlURL></service></serviceList></device></root>'''
        item=discovery.parse_description(xml,'http://192.168.1.20:1234/device.xml')[0]
        self.assertEqual(item['control_url'],'http://192.168.1.20:1234/control/av')
        self.assertEqual(item['control_port'],1234)

if __name__=='__main__': unittest.main()
