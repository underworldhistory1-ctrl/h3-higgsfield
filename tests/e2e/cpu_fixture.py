"""CPU-only browser acceptance fixture; real project/asset routes, mock Comfy API."""
import sys, pathlib, tempfile, json, asyncio
from aiohttp import web
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from h3_lab.routes import register_lab_routes
NODES = 'UNETLoader MiniMaxH3SigmaShift CLIPLoader VAELoader MiniMaxH3ImageToVideo MiniMaxH3ReferenceToVideo ConditioningZeroOut KSampler H3ReleaseForDecode VAEDecode VAEDecodeAudio CreateVideo H3SaveVideo LoadImage LoadVideo GetVideoComponents LoadAudio MiniMaxH3AddGuide'.split()
MODELS = {k: True for k in ['fl2va','ref2va','text_encoder','video_vae','audio_vae']}
captured=[]
fail_ack=False
continuation_enabled=False
ack_delay=0
image_standby=False
image_queue_mode='empty'
interrupt_count=0
@web.middleware
async def fixture(request, handler):
    global fail_ack, continuation_enabled, ack_delay, image_standby, image_queue_mode, interrupt_count
    path=request.path
    if path == '/h3_studio/lab/capabilities': return web.json_response({'add_guide':True,'guides_ready':True,'guide_audio':True,'guide_video':True,'continuation_ready':continuation_enabled,'models':MODELS,'missing_reasons':['CPU acceptance fixture: continuation unavailable']})
    if path == '/object_info/MiniMaxH3AddGuide': return web.json_response({'MiniMaxH3AddGuide':{'input':{'required':{'positive':['CONDITIONING'],'latent':['LATENT'],'frame_idx':['INT']},'optional':{'image':['IMAGE'],'audio':['AUDIO'],'vae':['VAE'],'audio_vae':['VAE']}}}})
    if path == '/__image_standby':
        image_standby=True
        return web.json_response({'ok':True})
    if path == '/h3_studio/image_readiness': return web.json_response({'ready':not image_standby,'inference_enabled':not image_standby,'reason':'Editing standby: inference is disabled while production renders use the shared GPU.','profiles':{'int8':{'ready':True},'bf16':{'ready':True}},'nodes':dict.fromkeys(['UNETLoader','CLIPLoader','VAELoader','TextEncodeQwenImage21','QwenImage21Cache','KSampler','EmptyLatentImage','VAEDecode','QwenStudioSaveImage','LoadImage','ImageScale'],True)})
    if path == '/h3_studio/image_library': return web.json_response({'items':[]})
    if path == '/system_stats': return web.json_response({'devices':[{'name':'CPU browser fixture'}]})
    if path == '/h3_studio/readiness': return web.json_response({'models':MODELS,'nodes':dict.fromkeys(NODES,True),'quality':{'h3_vae_tile_fix':True},'system_ram_limit_gb':128})
    if path == '/h3_studio/loras': return web.json_response({'loras':[]})
    if path == '/h3_studio/library': return web.json_response({'videos':[],'samples':[]})
    if path == '/__image_running':
        image_queue_mode='running';return web.json_response({'ok':True})
    if path == '/__image_pending':
        image_queue_mode='pending';return web.json_response({'ok':True})
    if path == '/__interrupt_count': return web.json_response({'count':interrupt_count})
    if path == '/interrupt':
        interrupt_count+=1;return web.json_response({'ok':True})
    if path == '/queue':
        current=next((item for item in reversed(captured) if item.get('image_prompt_id')),None)
        if request.method=='POST':
            body=await request.json()
            if current and current['image_prompt_id'] in body.get('delete',[]) and image_queue_mode=='pending': image_queue_mode='empty'
        item=[1,current['image_prompt_id'],current['prompt'],{},[]] if current else None
        return web.json_response({'queue_running':[item] if item and image_queue_mode=='running' else [],'queue_pending':[item] if item and image_queue_mode=='pending' else []})
    if path.startswith('/history'): return web.json_response({})
    if path == '/h3_studio/upload_ref':
        reader=await request.multipart();field=await reader.next();data=await field.read()
        kind=request.query.get('kind','image')
        return web.json_response({'name':f'fixture_{len(data)}_{field.filename}','width':1280,'height':704,'kind':kind,'fps':24,'frame_count':48,'duration':2,'has_audio':True})
    if path == '/prompt':
        record=await request.json();record['image_prompt_id']='qwen_fixture_'+str(len(captured)+1);captured.append(record);image_queue_mode='pending'
        return web.json_response({'prompt_id':record['image_prompt_id'],'number':1})
    if path == '/__fixture_fail_ack':
        fail_ack=True
        return web.json_response({'ok':True})
    if path == '/__delay_ack':
        ack_delay=1
        return web.json_response({'ok':True})
    if path == '/__enable_continuation':
        continuation_enabled=True
        return web.json_response({'ok':True})
    if path == '/__input_info':
        from PIL import Image
        from h3_lab.paths import owned_path
        file=owned_path(pathlib.Path(store.name)/'input',request.query['filename'])
        with Image.open(file) as image: return web.json_response({'width':image.width,'height':image.height})
    if path == '/view':
        from h3_lab.paths import owned_path
        return web.FileResponse(owned_path(pathlib.Path(store.name)/'output',request.query.get('subfolder','video')+'/'+request.query['filename']))
    if path == '/__captured': return web.json_response(captured)
    if path == '/ws':
        ws=web.WebSocketResponse();await ws.prepare(request)
        async for message in ws: pass
        return ws
    if path.startswith('/h3_studio/') and not path.startswith('/h3_studio/lab/'):
        return web.json_response({'ok':True})
    return await handler(request)
class FakeComfy:
    cancelled=False
    async def submit_prompt(self, spec, extra_data):
        global fail_ack, ack_delay
        self.cancelled=False
        prompt_id='fixture_prompt_'+str(len(captured)+1)
        captured.append({'prompt':spec['workflow'],'extra_data':extra_data,'prompt_id':prompt_id})
        if fail_ack:
            fail_ack=False
            raise RuntimeError('Fixture accepted prompt but response was lost')
        if ack_delay:
            delay=ack_delay;ack_delay=0
            await asyncio.sleep(delay)
        return {'prompt_id':prompt_id,'number':1}
    async def get_queue(self): return {'queue_running':[], 'queue_pending':[[1,captured[-1]['prompt_id'], captured[-1]['prompt'],captured[-1]['extra_data'],[]]] if captured and not self.cancelled else []}
    async def get_history(self, prompt_id=None): return {}
    async def delete_from_queue(self, ids):
        self.cancelled=True
        return {'ok':True}
    async def interrupt(self): return {'ok':True}
app=web.Application(middlewares=[fixture]);store=tempfile.TemporaryDirectory(prefix='h3-browser-fixture-')
register_lab_routes(app,store.name,input_root=pathlib.Path(store.name)/'input',output_root=pathlib.Path(store.name)/'output',comfy_client=FakeComfy())
from PIL import Image
image_dir=pathlib.Path(store.name)/'output'/'images';image_dir.mkdir(parents=True)
Image.new('RGB',(1280,704),(25,70,115)).save(image_dir/'qwen_studio_fixture.png')
import subprocess, wave
fixture_media=pathlib.Path(store.name)/'fixture_media';fixture_media.mkdir()
with wave.open(str(fixture_media/'voice.wav'),'wb') as audio:
    audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(32000);audio.writeframes(b'\0'*(32000*2*2))
subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=blue:s=1280x704:r=24:d=2','-f','lavfi','-i','anullsrc=r=32000:cl=mono','-t','2','-c:v','libx264','-preset','ultrafast','-c:a','aac',str(fixture_media/'motion.mp4')],check=True)
subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=green:s=320x180:r=25:d=3','-c:v','libx264','-threads','1',str(fixture_media/'external-25fps.mp4')],check=True)
import shutil
video_dir=pathlib.Path(store.name)/'output'/'video';video_dir.mkdir()
shutil.copy2(fixture_media/'motion.mp4',video_dir/'h3_studio_abcdef123456.mp4')
app.router.add_static('/__fixtures/',fixture_media)
app.router.add_static('/extensions/h3_studio/',ROOT/'web',show_index=True)
web.run_app(app,host='127.0.0.1',port=int(sys.argv[1] if len(sys.argv)>1 else 8768),print=None)
