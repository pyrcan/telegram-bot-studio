// Synthetic user-management browser regression. Use the isolated synthetic preview on port8768; never a live app.
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
  const draft={name:'Access fixture',description:'Fixture',instructions:'Fixture',model:'fixture-model',runtime:'conversation-v1'};
  const bot={id:'access-fixture',...draft,version:1,connection:{username:'fixture_bot',state:'active',epoch:'connection-fixture'},development:{uses_model:true,status:'Live'}};
  let people=[{user_id:88,name:'Sample tester',username:'sample_tester',created:1700000000,state:'pending',owner:false,review:'review-1'},
   {user_id:77,name:'Owner',created:1700000000,state:'allowed',owner:true,review:'owner'}];
  let actions=[];
  window.fetch=async function(url,options={}){
   const path=String(url);if(!path.includes('/service/'))throw Error('Unexpected network request: '+path);
   let data={};assert(options.method!=='POST','Browser must not directly mutate access');
   if(path.endsWith('owner/catalog'))data={assistants:[structuredClone(bot)],deleted:[]};
   else if(path.endsWith('/admin/state'))data={draft,revision:'r'};
   else if(path.endsWith('/access/users'))data={users:structuredClone(people),epoch:'connection-fixture',generation:0,policy:'owner-approval'};
   else throw Error('Unexpected intercepted read: '+path);
   return new Response(JSON.stringify(data),{headers:{'Content-Type':'application/json'}});
  };
  window.parent.postMessage=value=>{assert(value.type==='moebius:new-chat'&&!value.autoSend,'Editable draft only');actions.push(JSON.parse(value.draft.slice(value.draft.indexOf('{'))));};
  try{
   window.dispatchEvent(new MessageEvent('message',{source:window.parent,origin:window.location.origin,data:{type:'moebius:frame-init',token:'isolated-ui-fixture'}}));await frames();
   if(button('Your bots'))await click('Your bots');window.dispatchEvent(new Event('focus'));await frames();
   await click('Open workspace');await click('Users');
   assert(document.body.textContent.includes('Sample tester'),'Pending person visible');assert(!document.body.textContent.includes('Owner · You'),'Pending filter must not expose allowed rows');
   await click('Allow…');assert(actions[0].context.user_id===88&&actions[0].context.review==='review-1','Exact identity and review bound');assert(!!button('Allow…'),'Cancellation must leave request pending');
   await click('Allow…');assert(people[0].state==='pending','Request does not grant access');people[0].state='allowed';people[0].review='next-2';await click('Refresh users');assert(document.body.textContent.includes('No requests waiting'),'Successful approval refreshes list');
   await click('Allowed 2');assert(document.body.textContent.includes('Owner · You'),'Owner is visible but protected');assert([...document.querySelectorAll('button')].filter(b=>b.textContent==='Remove access…').length===1,'Owner must not have removal button');
   await click('Remove access…');assert(people[0].state==='allowed','Request does not revoke access');people[0].state='blocked';people[0].review='next-3';await click('Refresh users');await click('Blocked 1');assert(!!button('Allow again…'),'Blocked person remains recoverable');
   await click('Allow again…');people[0].state='allowed';people[0].review='next-4';await click('Refresh users');await click('Allowed 2');assert(!!button('Remove access…'),'Reapproval returns allowed row');
   assert(actions.map(a=>a.context.operation).join(',')==='allow,allow,block,allow','Requested operations preserved');
   return {ok:true,checks:['pending list','exact identity confirmation','cancel preserves request','approval refresh','owner protected','removal confirmation','restore access'],syntheticActions:actions.length};
  }finally{
   window.fetch=originalFetch;window.parent.postMessage=originalPost;
   if(button('Your bots'))button('Your bots').click();await frames();
  }
 })()`);console.log(JSON.stringify(result));
 }finally{ws.close()}
})().catch(e=>{console.error(e.message);process.exitCode=1});
