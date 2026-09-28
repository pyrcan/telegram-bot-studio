// Synthetic browser regression. Use the isolated synthetic preview on port8768; never a live app.
// All service requests and owner actions are intercepted; no live mutation.
const fs=require('fs');
(async()=>{
 const port=fs.readFileSync(process.env.AGENT_BROWSER_PROFILE+'/DevToolsActivePort','utf8').split('\n')[0];
 const targets=await(await fetch('http://127.0.0.1:'+port+'/json/list')).json();
 const target=targets.find(t=>t.type==='page'&&t.url==='http://127.0.0.1:8768/studio.html');
 if(!target)throw Error('Open the isolated Studio fixture first');
 const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise(r=>ws.onopen=r);
 let id=0;const pending=new Map();ws.onmessage=e=>{const m=JSON.parse(e.data);if(pending.has(m.id)){pending.get(m.id)(m);pending.delete(m.id)}};
 const ev=expression=>new Promise((res,rej)=>{const n=++id;pending.set(n,m=>m.error||m.result.exceptionDetails?rej(Error(JSON.stringify(m.error||m.result.exceptionDetails))):res(m.result.result.value));ws.send(JSON.stringify({id:n,method:'Runtime.evaluate',params:{expression,awaitPromise:true,returnByValue:true}}))});
 try{
 const result=await ev(`(async()=>{
  const originalFetch=window.fetch,originalPost=window.parent.postMessage;
  if(!window.__syntheticTelegramPreview)throw Error('Synthetic fixture required');
  const frames=async()=>{await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))};
  const assert=(v,msg)=>{if(!v)throw Error(msg)};
  const button=text=>[...document.querySelectorAll('button')].find(b=>b.textContent.replaceAll(' ','').trim()===text.replaceAll(' ',''));
  const click=async text=>{const b=button(text);assert(b&&!b.disabled,'Missing/enabled button: '+text);b.click();await frames()};
  const draft={name:'Publishing fixture',description:'A fixture introduction',instructions:'fixture',model:'fixture-model',runtime:'conversation-v1'};
  let bot={id:'publishing-fixture',...draft,draft_model:'fixture-model',revision:'r1',generation:0,version:null,draft_changed:true,
   connection:{username:'fixture_bot',state:'testing',epoch:'connection-fixture',owner_joined:true},
   private_test:{enabled:true,current:true,epoch:'test-fixture'},
   development:{can_publish:true,uses_model:true,status:'Private test',test_label:'Latest draft loaded',changed_fields:['Introduction']},
   release_review:{onboarding:{description:'DRAFT introduction',welcome:'DRAFT welcome'},uses_model:true,daily_task_limit:50,pending_task_limit:2},
   onboarding:{description:'LIVE introduction',welcome:'LIVE welcome'}};
  let actions=[],writes=[],failAction=false;
  window.fetch=async function(url,options={}){
   const path=String(url);if(!path.includes('/service/'))throw Error('Unexpected network request: '+path);
   let data={};if(options.method==='POST'){
    writes.push(path);
    if(path.endsWith('/telegram/pause')){bot.connection.state='paused';data={ok:true}}
    else throw Error('Unexpected intercepted write: '+path);
   }else if(path.endsWith('owner/catalog'))data={assistants:[structuredClone(bot)],deleted:[]};
   else if(path.endsWith('/admin/state'))data={draft,revision:bot.revision};
   else throw Error('Unexpected intercepted read: '+path);
   return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
  };
  window.parent.postMessage=value=>{assert(value.type==='moebius:new-chat'&&!value.autoSend,'Editable draft only');if(failAction)throw Error('Synthetic action failure');actions.push(JSON.parse(value.draft.slice(value.draft.indexOf('{'))));};
  try{
   window.dispatchEvent(new MessageEvent('message',{source:window.parent,origin:window.location.origin,data:{type:'moebius:frame-init',token:'isolated-ui-fixture'}}));await frames();
   if(button('Your bots'))await click('Your bots');window.dispatchEvent(new Event('focus'));await frames();
   await click('Open workspace');await click('Publish & share');
   assert(button('Review release').disabled,'Untested draft must not be treated as verified');
   await click('2 Review');assert(button('Confirm publication…').disabled,'Review navigation bypassed test acknowledgement');
   assert(document.body.textContent.includes('DRAFT welcome')&&!document.body.textContent.includes('LIVE welcome'),'Review must show draft not live copy');
   await click('Back to test');document.querySelector('input[type=checkbox]').click();await frames();await click('Review release');
   await click('Confirm publication…');assert(actions.length===1&&actions[0].context.revision==='r1','Exact reviewed revision required');
   assert(!button('Copy bot link'),'Cancelled approval must not open sharing');
   failAction=true;await click('Confirm publication…');assert(document.body.textContent.includes('Synthetic action failure'),'Failure must be visible');assert(!button('Copy bot link'),'Failure must not publish');failAction=false;
   bot.revision='r2';bot.private_test.current=false;bot.development.can_publish=false;window.dispatchEvent(new Event('focus'));await frames();
   assert(button('Review release').disabled&&!document.querySelector('input[type=checkbox]').checked,'Draft change must invalidate verification');
   bot.private_test.current=true;bot.private_test.epoch='test-fixture-2';bot.development.can_publish=true;window.dispatchEvent(new Event('focus'));await frames();
   document.querySelector('input[type=checkbox]').click();await frames();await click('Review release');await click('Confirm publication…');assert(!bot.version,'Opening a request must not publish');
   bot.version=1;bot.connection.state='active';bot.private_test.enabled=false;bot.private_test.current=false;bot.development.can_publish=false;bot.draft_changed=false;window.dispatchEvent(new Event('focus'));await frames();
   assert(!!button('Copy bot link'),'Successful approval must lead to sharing');assert(writes.length===0,'Publication must not approve users automatically');
   assert(document.body.textContent.includes('Request access'),'Sharing must explain approval');
   await click('Pause all replies');assert(!button('Copy bot link')&&!!button('Resume published bot…'),'Paused bot must not offer joining');
   assert(actions.every(a=>a.action==='publish-tested'),'Unexpected privileged action');
   return {ok:true,checks:['untested disabled','draft-specific review','cancel safe','failure safe','revision invalidates check','exact source confirmation','share after completion','approval-only sharing','paused sharing blocked'],syntheticActions:actions.length,syntheticWrites:writes.length};
  }finally{
   window.fetch=originalFetch;window.parent.postMessage=originalPost;
   if(button('Your bots'))button('Your bots').click();await frames();
  }
 })()`);console.log(JSON.stringify(result));
 }finally{ws.close()}
})().catch(e=>{console.error(e.message);process.exitCode=1});
