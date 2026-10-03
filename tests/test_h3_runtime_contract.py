"""CPU-only tests for lab context nodes and the real HTTP queue bridge."""
import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import torch
from safetensors.torch import save_file
from aiohttp import web
from aiohttp.test_utils import TestServer
from h3_queue_bridge import ComfyQueueBridge

class RuntimeContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        folder=types.SimpleNamespace(get_output_directory=lambda:self.tmp.name)
        spec=importlib.util.spec_from_file_location('h3_runtime_test',pathlib.Path(__file__).parents[1]/'h3_continuation.py')
        self.mod=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'folder_paths':folder}):spec.loader.exec_module(self.mod)
        self.video=torch.zeros(1,24,52,2,2,dtype=torch.float16)
        self.audio=torch.zeros(1,32,2,292,dtype=torch.float32)
        self.tensors={'nested_0':self.video,'nested_1':self.audio}
    def tearDown(self):self.tmp.cleanup()
    def test_real_safetensors_context_integrity_and_compatibility(self):
        token='a'*12;source=pathlib.Path(self.tmp.name)/'source.safetensors';save_file(self.tensors,str(source))
        self.mod.preserve_context(source,token,{'1':{'inputs':{'unet_name':'model.safetensors'}},'6':{'inputs':{'width':32,'height':32,'length':175}}},self.tensors)
        nested=types.SimpleNamespace(NestedTensor=lambda parts:parts)
        with patch.dict(sys.modules,{'comfy':types.ModuleType('comfy'),'comfy.nested_tensor':nested}):
            result=self.mod.H3LabLoadContext().load(token,'model.safetensors',32,32,24)[0]['samples']
            self.assertEqual(result[0].dtype,torch.float16)
            with self.assertRaises(ValueError):self.mod.H3LabLoadContext().load(token,'other.safetensors',32,32,24)
            with self.assertRaises(ValueError):self.mod.H3LabLoadContext().load(token,'model.safetensors',32,32,30)
            path=self.mod.context_directory()/(token+'.safetensors');changed=path.with_suffix('.changed');changed.write_bytes(path.read_bytes()+b'changed');changed.replace(path)
            with self.assertRaisesRegex(ValueError,'checksum'):self.mod.H3LabLoadContext().load(token,'model.safetensors',32,32,24)
    def test_non_av_and_wrong_canvas_fail_closed(self):
        with self.assertRaises(ValueError):self.mod.validate_av({'samples':self.video},32,32)
        with self.assertRaises(ValueError):self.mod.validate_av(self.tensors,64,32)
        with self.assertRaises(ValueError):self.mod.validate_av({**self.tensors,'nested_1':torch.zeros(1,32,1,292)},32,32)
    def test_temporal_grid_and_batch_ranks_fail_closed(self):
        self.mod.validate_av(self.tensors,32,32,175)
        for bad in (174, True, 124):
            with self.assertRaises(ValueError):self.mod.validate_av(self.tensors,32,32,bad)
        with self.assertRaises(ValueError):self.mod.validate_av({'nested_0':self.video[0],'nested_1':self.audio[0]},32,32,175)
        with self.assertRaises(ValueError):self.mod.validate_av({**self.tensors,'nested_1':torch.zeros(1,32,2,291)},32,32,175)
    def test_trim_exact_unique_frame_and_audio_sample_counts(self):
        images=torch.zeros(175,2,2,3);audio={'sample_rate':48000,'waveform':torch.arange(350000).reshape(1,1,-1)}
        out,sound=self.mod.H3LabTrimAV().trim(images,audio,39)
        self.assertEqual(len(out),136);self.assertEqual(sound['waveform'].shape[-1],272000)
        self.assertEqual(sound['waveform'][0,0,0],78000)
        with self.assertRaises(ValueError):self.mod.H3LabTrimAV().trim(images,{'sample_rate':48000,'waveform':torch.zeros(1,1,10)},39)

class QueueBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_loopback_submission_and_definite_rejection(self):
        app=web.Application();received=[]
        async def prompt(request):
            body=await request.json();received.append(body)
            if body['prompt'].get('reject'):return web.json_response({'error':'invalid workflow'},status=400)
            return web.json_response({'prompt_id':'owned-prompt'})
        app.router.add_post('/prompt',prompt)
        server=TestServer(app);await server.start_server()
        try:
            client=ComfyQueueBridge(lambda:server.port)
            result=await client.submit_prompt({'workflow':{'1':{'class_type':'Mock'}},'client_id':'client'},{'h3_lab_job_id':'job'})
            self.assertEqual(result['prompt_id'],'owned-prompt')
            self.assertEqual(received[0]['extra_data']['extra_pnginfo']['h3_lab_job_id'],'job')
            with self.assertRaises(ValueError):await client.submit_prompt({'workflow':{'reject':True}}, {})
            with self.assertRaises(ValueError):await client.interrupt_owned('foreign')
        finally:await server.close()
