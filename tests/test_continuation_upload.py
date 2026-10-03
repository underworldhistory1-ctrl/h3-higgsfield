import pathlib
import subprocess
import tempfile
import unittest
import array
from aiohttp import web, FormData
from aiohttp.test_utils import AioHTTPTestCase
from h3_lab.assembly import get_ffmpeg_path, inspect_media
from h3_lab.video_source import prepare_video_source, require_cpu_headroom
from h3_lab.routes import register_lab_routes


def make_video(path, duration=3, audio=False):
    args=[get_ffmpeg_path(), '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        f'color=c=green:s=320x180:r=25:d={duration}']
    if audio:
        args+=['-f','lavfi','-i',f'sine=frequency=440:sample_rate=48000:duration={duration}']
    args+=['-c:v','libx264','-threads','1','-pix_fmt','yuv420p']
    if audio: args+=['-c:a','aac','-shortest']
    subprocess.run(args+[str(path)],check=True,capture_output=True,timeout=30)


class SourcePreparationTests(unittest.TestCase):
    def test_memory_guard_honors_host_and_container_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            root=pathlib.Path(folder);mem=root/'meminfo';cg=root/'cgroup';cg.mkdir()
            mem.write_text('MemAvailable: 300000 kB\n')
            with self.assertRaisesRegex(ValueError,'memory is busy'): require_cpu_headroom(mem,cg)
            mem.write_text('MemAvailable: 4000000 kB\n')
            (cg/'memory.max').write_text(str(1024*1024*1024))
            (cg/'memory.current').write_text(str(900*1024*1024))
            (cg/'memory.stat').write_text('inactive_file 0\n')
            with self.assertRaisesRegex(ValueError,'memory is busy'): require_cpu_headroom(mem,cg)
            (cg/'memory.stat').write_text('inactive_file '+str(600*1024*1024)+'\n')
            require_cpu_headroom(mem,cg)

    def test_delayed_audio_keeps_its_opening_gap(self):
        with tempfile.TemporaryDirectory() as folder:
            source=pathlib.Path(folder)/'delayed.mp4';target=pathlib.Path(folder)/'prepared.mp4'
            subprocess.run([get_ffmpeg_path(),'-nostdin','-v','error','-y','-f','lavfi','-i','color=s=320x180:r=25:d=4',
                '-itsoffset','2','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2',
                '-c:v','libx264','-threads','1','-c:a','aac',str(source)],check=True,capture_output=True,timeout=30)
            prepare_video_source(source,target,256,256,3)
            raw=subprocess.check_output([get_ffmpeg_path(),'-v','error','-i',str(target),'-map','0:a:0','-ac','1','-ar','48000','-f','f32le','pipe:1'])
            samples=array.array('f');samples.frombytes(raw)
            self.assertLess(max(abs(x) for x in samples[:24000]),0.001)
            self.assertGreater(max(abs(x) for x in samples[72000:96000]),0.01)

    def test_extreme_aspect_crops_before_scaling(self):
        with tempfile.TemporaryDirectory() as folder:
            source=pathlib.Path(folder)/'wide.mp4';target=pathlib.Path(folder)/'prepared.mp4'
            subprocess.run([get_ffmpeg_path(),'-nostdin','-v','error','-y','-f','lavfi','-i','color=s=8192x32:r=24:d=2',
                '-c:v','libx264','-threads','1',str(source)],check=True,capture_output=True,timeout=30)
            metadata=prepare_video_source(source,target,256,256,2)
            self.assertEqual(metadata['frame_count'],48)

    def test_audio_tail_is_frame_exact_and_fitted(self):
        with tempfile.TemporaryDirectory() as folder:
            source=pathlib.Path(folder)/'source.mp4';target=pathlib.Path(folder)/'prepared.mp4'
            make_video(source,audio=True)
            m=prepare_video_source(source,target,256,256,2,'contain')
            self.assertEqual(m['frame_count'],48)
            self.assertEqual(m['source_start_seconds'],1)
            self.assertTrue(m['source_has_audio'])
            streams=inspect_media(target)['streams'];v=next(s for s in streams if s['codec_type']=='video')
            self.assertEqual((v['width'],v['height']), (256,256))
            self.assertEqual(v['r_frame_rate'],'24/1')
            self.assertEqual(int(v['nb_read_frames']),48)
            self.assertTrue(any(s['codec_type']=='audio' for s in streams))

    def test_short_source_rejected_without_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source=pathlib.Path(folder)/'source.mp4';target=pathlib.Path(folder)/'prepared.mp4'
            make_video(source,duration=1)
            with self.assertRaisesRegex(ValueError,'40 frames'): prepare_video_source(source,target,256,256)
            self.assertFalse(target.exists())

    def test_invalid_preparation_options_fail_before_probe(self):
        for width,height,seconds,fit in [(255,256,5,'crop'),(256,256,float('nan'),'crop'),(256,256,16,'crop'),(256,256,5,'stretch')]:
            with self.assertRaises(ValueError): prepare_video_source('missing.mp4','missing-output.mp4',width,height,seconds,fit)


class ContinuationUploadRoutes(AioHTTPTestCase):
    async def get_application(self):
        self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name)
        self.app=web.Application(client_max_size=501*1024*1024)
        self.services=register_lab_routes(self.app,str(self.root),output_root=self.root,input_root=self.root/'input')
        self.project=self.services['projects'].create_project('Upload acceptance',{'width':256,'height':256})
        return self.app

    def tearDown(self):
        super().tearDown();self.temp.cleanup()

    async def upload(self, filename, data, **query):
        form=FormData();form.add_field('file',data,filename=filename,content_type='application/octet-stream')
        fields={'project_id':self.project['project_id'],'width':'256','height':'256','keep_seconds':'2','fit':'crop',**query}
        return await self.client.post('/h3_studio/lab/media/upload_continuation',params=fields,data=form)

    async def test_silent_source_owned_take_and_portable_bundle(self):
        source=self.root/'external.mp4';make_video(source)
        response=await self.upload('my-scene.mp4',source.read_bytes());self.assertEqual(response.status,201)
        result=await response.json();self.assertFalse(result['metadata']['source_has_audio'])
        self.assertEqual(result['metadata']['frame_count'],48)
        self.assertEqual(result['metadata']['context_frames'],39)
        self.assertTrue((self.root/result['take']['output_file']).is_file())
        asset=self.services['assets'].get_asset(result['asset']['asset_id'])
        self.assertTrue((self.root/'lab_storage'/asset['server_path']).is_file())
        projects=self.services['projects'];project=projects.get_project(self.project['project_id'])
        self.assertEqual(len(project['takes']),1);self.assertEqual(project['accepted_take_ids'],[])
        project['draft']={'continuation':{'type':'imported','source_asset_id':asset['asset_id'],'source_take_id':result['take']['take_id']}}
        projects.save_project(project['project_id'],project,project['revision'])
        bundle=projects.export_bundle(project['project_id'])
        restored=projects.import_bundle(bundle)
        self.assertNotEqual(restored['project_id'],project['project_id'])
        self.assertTrue((self.root/restored['takes'][0]['output_file']).is_file())
        self.assertIsNotNone(self.services['assets'].get_asset(restored['draft']['continuation']['source_asset_id']))
        self.assertFalse(list((self.root/'lab_storage').glob('upload_source_*')))

    async def test_upload_requires_project(self):
        response=await self.upload('a.mp4',b'not-a-video',project_id='not-a-project')
        self.assertEqual(response.status,400)
        self.assertFalse(self.services['assets']._assets)

    async def test_fake_video_and_wrong_extension_leave_no_take(self):
        for filename in ['scene.mp4','scene.txt']:
            response=await self.upload(filename,b'not-a-video')
            self.assertEqual(response.status,400)
        self.assertFalse(self.services['projects'].get_project(self.project['project_id']).get('takes',[]))
        self.assertFalse(list((self.root/'lab_storage').glob('upload_source_*')))
