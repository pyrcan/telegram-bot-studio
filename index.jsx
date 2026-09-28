import React,{useState,useEffect,useRef} from 'react'
import {Plus,BookOpen,ArrowUpRight,ArrowLeft} from '@openai/apps-sdk-ui/components/Icon'
import {CSS} from './simple-theme.js'
import {ownerRequest} from './owner-handoff.js'

export default function App({appId,token}){
 const [source,setSource]=useState(null)
 const [deletedBots,setDeletedBots]=useState([])
 const [bots,setBots]=useState(null),[view,setView]=useState('list'),[selected,setSelected]=useState(null)
 const [error,setError]=useState(''),[busy,setBusy]=useState(false),[notice,setNotice]=useState('')
 const [draft,setDraft]=useState(null),[detail,setDetail]=useState(null),[mode,setMode]=useState(null)
 const createKey=useRef(crypto.randomUUID()),dirty=useRef(false),latest=useRef(null),generation=useRef(0),reads=useRef(new Map()),readEpoch=useRef(0)
 const req=async(path,body)=>{if(body!==undefined){readEpoch.current++;reads.current.clear()}const r=await fetch(`/api/apps/${appId}/service/${path}`,{method:body===undefined?'GET':'POST',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});const v=await r.json();if(!r.ok)throw new Error(v.error||v.detail||'Could not finish. Please try again.');return v}
 // Share only in-flight reads, never cache credentials or participant data.
 const read=path=>{if(reads.current.has(path))return reads.current.get(path);const pending=req(path).finally(()=>{if(reads.current.get(path)===pending)reads.current.delete(path)});reads.current.set(path,pending);return pending}
 const load=async()=>{const epoch=readEpoch.current;const v=await read('owner/catalog');if(epoch===readEpoch.current){setBots(v.assistants);setDeletedBots(v.deleted||[])}return v}
 const refresh=async()=>{const id=latest.current,gen=generation.current,epoch=readEpoch.current;const [,v]=await Promise.all([load(),id?read(`assistants/${id}/admin/state`):null]);if(v&&gen===generation.current&&epoch===readEpoch.current&&latest.current===id&&!dirty.current){setDetail(v);setDraft(v.draft)}}
 const run=async fn=>{setBusy(true);setError('');setNotice('');try{return await fn()}catch(e){if(e.code!=='aborted'&&e.name!=='AbortError'){setError(e.message);window.mobius?.signal('error',{message:e.message,source:'studio'})}}finally{setBusy(false)}}
 // A retained app frame receives renewed credentials without remounting.
 // Rebind background readers and retire their in-flight results with that
 // identity change; never keep the mount-time bearer in focus/subscription callbacks.
 useEffect(()=>{
   let active=true
   readEpoch.current++;reads.current.clear();setError('')
   const report=e=>{if(active)setError(e.message)}
   refresh().then(()=>{if(active)window.mobius?.signal('app_ready')}).catch(report)
   const returned=()=>{if(active&&!document.hidden)refresh().catch(report)}
   window.addEventListener('focus',returned)
   document.addEventListener('visibilitychange',returned)
   const unsub=window.mobius.storage.subscribe('revision.json',()=>{if(active)refresh().catch(report)})
   return()=>{active=false;window.removeEventListener('focus',returned);document.removeEventListener('visibilitychange',returned);unsub()}
 },[appId,token])

 const back=()=>{generation.current++;setView('list');setSelected(null);latest.current=null;setDetail(null);setMode(null);setError('');setNotice('');dirty.current=false;load().catch(e=>setError(e.message))}
 const open=async bot=>{const gen=++generation.current,epoch=readEpoch.current;setSelected(bot.id);latest.current=bot.id;dirty.current=false;setDetail(null);setDraft(null);setView('setup');setMode(null);setError('');setNotice('');try{const v=await read(`assistants/${bot.id}/admin/state`);if(gen===generation.current&&epoch===readEpoch.current&&!dirty.current){setDetail(v);setDraft(v.draft)}}catch(e){if(gen===generation.current)setError(e.message)}}
 const edit=(key,value)=>{dirty.current=true;setDraft(d=>({...d,[key]:value}))}
 const create=()=>run(async()=>{let v;try{v=await req('owner/create',{name:draft.name,request_id:createKey.current,...(source?{source:{id:source.id,revision:source.revision}}:{})})}catch(e){await load();throw e};createKey.current=crypto.randomUUID();await load();await open({id:v.id});window.mobius?.signal('item_created',{type:'assistant'})})
 const save=()=>run(async()=>{await req(`assistants/${selected}/admin/draft`,{...draft,expected_revision:detail.revision});dirty.current=false;await refresh();setNotice('Draft saved. The running bot is unchanged.')})

 const bot=bots?.find(b=>b.id===selected),isNotes=(bot?.published_runtime||bot?.runtime)==='notes-v1',connection=bot?.connection,connected=!!connection?.username,active=connection?.state==='active',testing=bot?.private_test?.enabled,testingCurrent=bot?.private_test?.current,development=bot?.development,usesModel=development?.uses_model===true
 const requestOwner=(action,context)=>{
   ownerRequest(appId,'Telegram Bot Studio',action,context)
   setNotice('Request prepared in Möbius chat. Review and send it there; nothing has changed yet.')
 }
 const setup=(resume=false)=>run(async()=>{
   requestOwner(resume?'activate-telegram':'connect-telegram',{assistant:selected,...(resume?{bot:connection.username,epoch:connection.epoch}:{})})
 })
 const develop=action=>run(async()=>{
   if(dirty.current)throw new Error('Save your draft before testing or publishing it.')
   requestOwner(action,{assistant:selected,name:bot.name,bot:connection.username,model:bot.draft_model,runtime:bot.runtime,revision:bot.revision,epoch:connection.epoch,generation:bot.generation,test_epoch:bot.private_test?.epoch||null})
 })
 const customize=()=>run(async()=>{
   if(bot.editing_chat?.id){
     window.parent.postMessage({type:'moebius:open-chat',chatId:bot.editing_chat.id},'*')
   }else requestOwner('customize-bot',{assistant:selected,name:bot.name})
 })
 const lifecycle=(target,restore=false)=>run(async()=>{
   requestOwner('bot-lifecycle',{assistant:target.id,name:target.name,generation:target.generation,operation:restore?'restore':'delete',...(!restore?{revision:target.revision,epoch:target.connection?.epoch||null}:{})})
 })
 const pause=()=>run(async()=>{await req(`assistants/${selected}/telegram/pause`,{});await refresh();setNotice('Paused. Saved data is unchanged.');return true})
 const botLink=(c,primary=true)=><a className={`bs-btn ${primary?'bs-primary':''}`}  href={`https://t.me/${c.username}`} target="_blank" rel="noopener noreferrer">Open in Telegram<ArrowUpRight/></a>
 return <div className="bs-root"><style>{CSS}</style><main className="bs-page">
  <header className="bs-header"><div>{view==='list'?<><h1>Telegram Bot Studio</h1><p>Build here. Test in Telegram. Release when ready.</p></>:<><button className="bs-text" disabled={busy} onClick={back}><ArrowLeft width={16}/> Your bots</button><h1>{view==='create'?'Create a bot':bot?.name||'Your bot'}</h1></>}</div>{view==='list'&&<button className="bs-btn bs-primary" onClick={()=>{createKey.current=crypto.randomUUID();setSource(null);setDraft({name:''});setView('create');setError('');setNotice('')}}><Plus/>Create bot</button>}</header>
  {error&&<div className="bs-error" role="alert">{error}</div>}{notice&&<p className="bs-notice" role="status">{notice}</p>}
  {view==='list'&&<>{bots===null?<p className="bs-muted">Loading your bots…</p>:bots.length===0?<div className="bs-empty"><BookOpen/><h2>A small helper starts here.</h2><p className="bs-muted">Name it, connect Telegram, then build your first private test.</p></div>:bots.map(b=><section className="bs-card" key={b.id}><div className="bs-card-top"><div className="bs-mark">{(b.published_runtime||b.runtime)==='notes-v1'?<BookOpen/>:<Plus/>}</div><div className="bs-bot-name"><h2>{b.name}</h2><span className="bs-helper">{b.connection?.username?`@${b.connection.username}`:'Not connected to Telegram'}</span></div><span className={`bs-badge ${b.development?.status==='Live'?'ready':''}`}>{b.development?.status||'Checking status'}</span></div><p className="bs-helper">{b.development?.behavior_label} · {b.development?.release_label}</p><p className="bs-helper">{b.development?.next_step}</p><div className="bs-row bs-spaced"><button className="bs-btn bs-primary" disabled={busy} onClick={()=>open(b)}>{b.connection?.username?'Open workspace':'Connect Telegram'}</button>{['active','testing'].includes(b.connection?.state)&&botLink(b.connection,false)}</div></section>)}<p className="bs-footer">Set up and pair Telegram Bridge first; Studio uses it to verify your owner account when connecting a bot. Each bot serves its users separately. Remote management needs additional approval.</p>{deletedBots.length>0&&<details className="bs-advanced"><summary>Deleted bots ({deletedBots.length})</summary><p className="bs-helper">Recoverable removal, not data erasure. Data and connections are retained, including connection slots. Restore does not resume replies.</p>{deletedBots.map(b=><div className="bs-deleted" key={b.id}><div><strong>{b.name}</strong><p className="bs-helper">Deleted {new Date(b.deleted_at*1000).toLocaleDateString()}</p></div><button className="bs-btn" disabled={busy} onClick={()=>lifecycle(b,true)}>Restore</button></div>)}</details>}</>}
  {selected&&<nav className="bs-tabs" aria-label="Bot management">{[['setup','Build'],['publish','Publish & share'],['users','Users']].map(([v,label])=><button className="bs-btn" aria-current={view===v?'page':undefined} disabled={busy} key={v} onClick={()=>{setView(v);setError('');setNotice('')}}>{label}</button>)}</nav>}
  {view==='create'&&draft&&<CreateBot bots={bots} name={draft.name} setName={value=>edit('name',value)} source={source} choose={setSource} busy={busy} create={create}/>}

  {view==='setup'&&bot&&<section className="bs-card">
   <p className="bs-steps">CONNECT → BUILD → TEST → RELEASE</p>
   {bot.based_on&&<p className="bs-helper">Started from {bot.based_on.name} · {bot.based_on.version?`version ${bot.based_on.version}`:'unpublished draft'}. Your settings and user data are separate. Connect your own services and test before sharing.</p>}
   {!connected?<>
    {connection?.error&&<p className="bs-error" role="alert">{connection.error}</p>}
    {connection?.reconnect&&<div className="bs-notice"><p>@{connection.reconnect.bot} is retained by the deleted {connection.reconnect.source_name}. You can restore that original entry, or ask Möbius to move the connection here while keeping its old data separate.</p><button className="bs-btn" disabled={busy} onClick={()=>lifecycle({id:connection.reconnect.source,name:connection.reconnect.source_name,generation:connection.reconnect.source_generation},true)}>Restore original bot</button></div>}
    <h2>Connect Telegram first</h2><p className="bs-muted">Give this bot its own Telegram account, so you can try it while you build. Connecting does not turn on replies or publish it.</p>
    {!mode?<div className="bs-choice"><button className="bs-btn bs-primary" onClick={()=>setMode('new')}>Create a Telegram bot</button><button className="bs-btn" onClick={()=>setMode('existing')}>I already have a bot</button></div>:<>
     <ol className="bs-links"><li>Open <a href="https://t.me/BotFather" target="_blank" rel="noopener noreferrer">BotFather</a>.</li><li>{mode==='new'?<>Send <strong>/newbot</strong> and choose its name.</>:<>Choose your bot and open <strong>API Token</strong>.</>}</li><li>Continue in Möbius chat and enter the key only in its secure card.</li></ol>
     <button className="bs-btn bs-primary" disabled={busy||connection?.state==='checking'} onClick={()=>setup()}>Connect securely</button><p className="bs-helper">Continue in Möbius chat, then use its secure key card. Use a separate bot; never send its key as a chat message.</p><button className="bs-text" onClick={()=>setMode(null)}>Back</button>
    </>}
   </>:<>
    <div className="bs-card-top"><h2>@{connection.username}</h2><span className="bs-badge">{development?.status}</span></div>{connection.error&&<p role="alert" className="bs-error">{connection.error}</p>}
    <dl className="bs-development" aria-label="Development status">
     <div><dt>Your Telegram test</dt><dd>{development?.test_label}</dd></div>
     <div><dt>Published bot</dt><dd>{development?.release_label}</dd></div>
    </dl>
    <p className="bs-muted">{development?.next_step}</p>
    <div className="bs-row">
     {(!testing||!testingCurrent)&&<button className="bs-btn bs-primary" disabled={busy||dirty.current} onClick={()=>develop('test-draft')}>{testing?'Try latest changes':'Start private testing'}</button>}
     <button className={`bs-btn ${testingCurrent?'bs-primary':''}`} disabled={busy} onClick={customize}>Customize<ArrowUpRight/></button>
     {(testing||active)&&botLink(connection,false)}
    </div>
    {bot.runtime==='blank-v1'&&<p className="bs-helper">A clean start: no preset purpose or AI calls. Customize it to add functionality.</p>}
    <p className="bs-helper">Customize opens this bot’s owner chat. Changes stay private until you release them.</p>
    <details className="bs-advanced"><summary>How private testing works</summary><p className="bs-helper">Saving a draft does not load it into Telegram. Try latest changes loads it for your paired account only; approved users keep the published version, if it is running. A loaded draft is not proof that it works.</p><p className="bs-helper">Each changed test starts a fresh conversation. Earlier test data is retained separately, never mixed with users’ data. Your development chat stays the same.</p></details>
    <div className="bs-release-link"><p className="bs-helper">Ready for other people? Review your tested draft, then share its access-request link.</p><button className="bs-btn" disabled={busy} onClick={()=>setView('publish')}>Publish & share</button></div>
   </>}
   <details className="bs-advanced"><summary>Bot details</summary><p className="bs-helper">Name and introduction are draft settings. Use Customize to build functionality.{!usesModel?' This bot’s current functionality does not use an AI model.':''}</p>{draft?<form className="bs-form" onSubmit={e=>{e.preventDefault();save()}}><label className="bs-label">Name<input className="bs-input" value={draft.name} maxLength={80} onChange={e=>edit('name',e.target.value)}/></label>{usesModel&&<label className="bs-label">Instructions for AI answers<textarea className="bs-input" value={draft.instructions} maxLength={6000} onChange={e=>edit('instructions',e.target.value)}/></label>}<label className="bs-label">Introduction<input className="bs-input" value={draft.description} maxLength={250} onChange={e=>edit('description',e.target.value)}/></label>{usesModel&&<details className="bs-advanced"><summary>Model</summary><label className="bs-label">Model<input className="bs-input" value={draft.model} onChange={e=>edit('model',e.target.value)}/></label><p className="bs-helper">Separate from your personal Möbius model.</p></details>}<p className="bs-helper">Save keeps a draft. Try latest changes loads it privately; publication remains separate.</p><button className="bs-btn bs-primary" disabled={busy||!detail||!draft.name.trim()||!draft.instructions.trim()}>Save draft</button></form>:<p>Loading details…</p>}</details>
   <details className="bs-advanced"><summary>Settings</summary>
    <button className="bs-btn" disabled={busy} onClick={()=>refresh().catch(e=>setError(e.message))}>Refresh status</button>
    {testing&&<button className="bs-btn" disabled={busy} onClick={()=>develop('stop-test')}>Stop private testing</button>}
    {connection?.state==='paused'&&!!bot.version&&<button className="bs-btn" disabled={busy} onClick={()=>setup(true)}>Resume published bot</button>}
    {(active||connection?.state==='testing')&&<button className="bs-text" disabled={busy} onClick={pause}>Pause all replies</button>}
    <p className="bs-helper">Delete removes this bot from Studio and stops replies. Data, connection and Telegram account are retained for recovery.</p>
    <button className="bs-btn bs-danger" disabled={busy} onClick={()=>lifecycle(bot)}>Delete bot…</button>
   </details>
  </section>}

  {view==='publish'&&bot&&<PublishFlow key={`${bot.id}:${bot.revision}:${connection?.epoch}:${bot.private_test?.epoch}`} bot={bot} busy={busy} dirty={dirty.current} develop={develop} build={()=>setView('setup')} pause={pause} resume={()=>setup(true)} botLink={botLink} copyLink={()=>run(async()=>{if(!await window.mobius.clipboard.writeText(`https://t.me/${connection.username}`))throw new Error('Could not copy the link.');setNotice('Bot link copied. New users still need your approval in Users.')})} users={()=>setView('users')}/>}

  {view==='users'&&bot&&<Users key={selected} appId={appId} token={token} bot={bot} busy={busy} run={run}/>}

 </main></div>
}


// Presentation only. Publication still goes through the source-bound trusted
// owner action; sharing a link never grants account access.
function PublishFlow({bot,busy,dirty,develop,build,pause,resume,botLink,copyLink,users}){
 const live=!!bot.version, active=bot.connection?.state==='active', current=bot.private_test?.current===true
 const [step,setStep]=useState(live&&!bot.draft_changed?'share':'test'),[verified,setVerified]=useState(false)
 const review=bot.release_review, canReview=current&&!dirty&&bot.development?.can_publish
 useEffect(()=>{if(live&&!bot.draft_changed)setStep('share')},[live,bot.version,bot.draft_changed])
 const publish=()=>develop('publish-tested')
 const titles={test:'Test',review:'Review',share:'Share'}
 return <section className="bs-card bs-publish">
  <div className="bs-card-top"><div><p className="bs-steps">PUBLISH & SHARE</p><h2>{step==='share'?'Share your bot':step==='review'?'One last look':'Try it as a user'}</h2></div><span className="bs-badge">{live?`Version ${bot.version} · ${active?'live':'paused'}`:'Not published'}</span></div>
  <nav className="bs-publish-steps" aria-label="Publishing steps">{Object.entries(titles).map(([id,label],i)=><button key={id} className="bs-text" aria-current={step===id?'step':undefined} disabled={busy||(id==='share'&&!live)} onClick={()=>setStep(id)}><span>{i+1}</span>{label}</button>)}</nav>
  {step==='test'&&<>
   <p className="bs-muted">Try a normal request and a failure case in Telegram. Only your paired account sees this draft; existing users keep their published version.</p>
   {dirty?<p className="bs-error">You have unsaved changes. Save them under Build before loading a test.</p>:review?.private_only?<p className="bs-error">This prototype is private-test-only. Customize a guest-ready version before publishing.</p>:<p className="bs-helper">{current?'Latest draft loaded — not automatically verified.':'Load the latest draft before reviewing a release.'}</p>}
   <div className="bs-row">{!current&&<button className="bs-btn bs-primary" disabled={busy||dirty||!bot.connection?.username} onClick={()=>develop('test-draft')}>{bot.private_test?.enabled?'Try latest changes':'Start private testing'}</button>}{current&&botLink(bot.connection)}</div>
   {(!bot.connection?.username||dirty||bot.runtime==='blank-v1'||review?.private_only)&&<button className="bs-text" disabled={busy} onClick={build}>Back to Build</button>}
   {bot.runtime==='blank-v1'&&<p className="bs-helper">Customize this blank bot before publishing it.</p>}
   <label className="bs-check"><input type="checkbox" checked={verified&&!!canReview} disabled={busy||!canReview} onChange={e=>setVerified(e.target.checked)}/><span>I tried this latest draft in Telegram and it behaved as expected.</span></label>
   <button className="bs-btn bs-primary" disabled={busy||!canReview||!verified} onClick={()=>setStep('review')}>Review release</button>
  </>}
  {step==='review'&&<>
   <div className="bs-audience"><strong>Approved people only</strong><p className="bs-helper">Every new person must request access and be approved by you in Users. Sharing a link never grants access. Existing approved users keep access.</p></div>
   <p className="bs-helper">{live?`Update version ${bot.version}. Changed settings: ${bot.development?.changed_fields.join(', ')||'none'}.`:'Your first release.'} This publishes the exact loaded draft, not future edits.</p>
   {review?<>
    <details className="bs-advanced" open><summary>What a new user sees</summary><p className="bs-steps">TELEGRAM INTRODUCTION</p><p className="bs-welcome">{review.onboarding.description}</p><p className="bs-steps">AFTER APPROVAL · START</p><p className="bs-welcome">{review.onboarding.welcome}</p><button className="bs-text" disabled={busy} onClick={build}>Edit under Build</button></details>
    <details className="bs-advanced"><summary>Privacy & usage</summary><p className="bs-helper">Conversations are separate between users. Messages and saved content stay on this Möbius server. As its operator, you can technically access them; this is not end-to-end encryption.</p><p className="bs-helper">{review.uses_model?`AI answers send relevant content to the configured model provider (${bot.draft_model}).`:'This draft does not use an AI model.'} Telegram and any services used by this bot also receive the data needed to handle requests.</p><p className="bs-helper">Up to {review.daily_task_limit} queued assistant requests per person in a rolling 24 hours, with at most {review.pending_task_limit} waiting or running. Individual bots may impose tighter limits. Simple commands and note saves do not count. These are request limits, not a spending cap.</p><p className="bs-helper">Runs on your current Möbius server, not separate hosting. You cover hosting and any model usage; no billing or user payments are enabled.</p></details>
   </>:<p className="bs-error">Release information is unavailable. Return to Build and refresh status.</p>}
   <p className="bs-helper">Publishing enables replies for you and approved users. Your Telegram chat returns to the live version; private test data stays separate. No new user is approved by publishing.</p>
   {(!canReview||!verified)&&<p className="bs-helper">First verify the latest draft in the Test step.</p>}
   <div className="bs-row"><button className="bs-btn bs-primary" disabled={busy||!review||!canReview||!verified} onClick={publish}>Confirm publication…</button><button className="bs-text" disabled={busy} onClick={()=>setStep('test')}>Back to test</button></div>
   <p className="bs-helper">Opens Möbius’s final approval. Nothing changes until you confirm.</p>
  </>}
  {step==='share'&&<>
   <p className="bs-muted">Share the bot link with your testers. They tap Start, then Request access. Approve each person in Users before they can use the bot. No Möbius account needed.</p>
   {bot.draft_changed&&<p className="bs-helper">Approved users get published version {bot.version}, not your unpublished changes.</p>}
   {!active?<><p className="bs-error">Replies are paused. Resume the published bot before accepting requests.</p>{bot.connection?.state==='paused'?<button className="bs-btn bs-primary" disabled={busy} onClick={resume}>Resume published bot…</button>:<button className="bs-btn" disabled={busy} onClick={()=>setStep('test')}>Review a tested release</button>}</>:<>
    <div className="bs-row"><button className="bs-btn bs-primary" disabled={busy} onClick={copyLink}>Copy bot link</button><button className="bs-btn" disabled={busy} onClick={users}>Review users</button></div><p className="bs-helper">Approval is tied to their Telegram account. Old invitation links no longer grant access automatically.</p>
    <details className="bs-advanced"><summary>Published welcome</summary><p className="bs-welcome">{bot.onboarding?.description}</p><p className="bs-welcome">{bot.onboarding?.welcome}</p></details>
    <div className="bs-release-link"><span className="bs-helper">Need to stop serving? Data is retained. Pending work stops; replies already sending may still arrive.</span><button className="bs-btn" disabled={busy} onClick={pause}>Pause all replies</button></div>
   </>}
  </>}
 </section>
}


function Users({appId,token,bot,busy,run}){
 const [data,setData]=useState(null),[error,setError]=useState(''),[filter,setFilter]=useState('pending')
 const sequence=useRef(0),refreshRef=useRef(null)
 useEffect(()=>{
  let active=true
  const refresh=async()=>{
   const seq=++sequence.current
   try{const r=await fetch(`/api/apps/${appId}/service/assistants/${bot.id}/access/users`,{headers:{Authorization:`Bearer ${token}`}});const v=await r.json();if(!r.ok)throw new Error(v.error||v.detail||'Could not load users.');if(active&&seq===sequence.current){setData(v);setError('')}}
   catch(e){if(active&&seq===sequence.current)setError(e.message)}
  }
  refreshRef.current=refresh;refresh()
  const returned=()=>{if(!document.hidden)refresh()}
  window.addEventListener('focus',returned);document.addEventListener('visibilitychange',returned)
  const unsubscribe=window.mobius.storage.subscribe('revision.json',refresh)
  return()=>{active=false;sequence.current++;refreshRef.current=null;unsubscribe();window.removeEventListener('focus',returned);document.removeEventListener('visibilitychange',returned)}
 },[appId,token,bot.id])
 const change=(person,operation)=>run(async()=>{
  ownerRequest(appId,'Telegram Bot Studio','change-access',{assistant:bot.id,bot:bot.connection?.username,user_id:person.user_id,name:person.name,operation,review:person.review,epoch:data.epoch,generation:data.generation})

 })
 const people=(data?.users||[]).filter(p=>p.state===filter),counts=Object.fromEntries(['pending','allowed','blocked'].map(s=>[s,(data?.users||[]).filter(p=>p.state===s).length]))
 return <section className="bs-card">
  <div className="bs-card-top"><div><p className="bs-steps">PRIVATE PILOT</p><h2>Who can use this bot</h2></div><button className="bs-text" disabled={busy} onClick={()=>refreshRef.current?.()}>Refresh users</button></div>
  <p className="bs-muted">New people need your approval in Möbius chat. Buttons prepare a request; opening one changes no access. This list shows identities only—not conversations or notes.</p>
  <nav className="bs-user-tabs" aria-label="User access status">{[['pending','Pending'],['allowed','Allowed'],['blocked','Blocked']].map(([id,label])=><button className="bs-btn" aria-current={filter===id?'page':undefined} key={id} onClick={()=>setFilter(id)}>{label} {data&&<span>{counts[id]}</span>}</button>)}</nav>
  {error&&<p role="alert" className="bs-error">{error}</p>}
  {!data?<p className="bs-helper">Loading users…</p>:people.length===0?<div className="bs-users-empty"><strong>{filter==='pending'?'No requests waiting':filter==='allowed'?'No approved users yet':'No blocked users'}</strong><p className="bs-helper">{filter==='pending'?'Share the bot link after publishing. People appear here when they tap Request access.':filter==='allowed'?'Approve a request to let that person use the bot.':'People you remove or decline will appear here. Their saved data stays intact.'}</p></div>:people.map(person=><div className="bs-user" key={person.user_id}><div className="bs-user-info"><strong>{person.name}{person.owner?' · You':''}</strong><p className="bs-helper">{person.username?`@${person.username} · `:''}Telegram ID {person.user_id}</p><p className="bs-helper">{person.state==='pending'?'Requested':'Joined / requested'} {new Date(person.created*1000).toLocaleDateString()}</p></div><div className="bs-row">{person.owner?<span className="bs-badge">Owner</span>:<>{person.state!=='allowed'&&<button className="bs-btn bs-primary" disabled={busy||bot.connection?.state!=='active'} onClick={()=>change(person,'allow')}>{person.state==='blocked'?'Allow again…':'Allow…'}</button>}{person.state!=='blocked'&&<button className="bs-btn" disabled={busy} onClick={()=>change(person,'block')}>{person.state==='pending'?'Decline…':'Remove access…'}</button>}</>}</div></div>)}
  {bot.connection?.state!=='active'&&<p className="bs-helper">Publish or resume this bot before approving people. Private testing remains owner-only.</p>}
  <p className="bs-footer">Approval uses the account ID, not a display name or username. Allow sends a short approval message. Removing access retains saved data and stops pending work; replies already sending may still arrive.</p>
 </section>
}


function CreateBot({bots,name,setName,source,choose,busy,create}){
 const [query,setQuery]=useState('')
 const starters=(bots||[]).map(b=>b.starter).filter(Boolean)
 const shown=starters.filter(s=>(s.name+' '+s.description).toLowerCase().includes(query.toLowerCase().trim()))
 const changed=source&&!starters.some(s=>s.id===source.id&&s.revision===source.revision)
 return <section className="bs-card"><form className="bs-form" onSubmit={e=>{e.preventDefault();if(!changed)create()}}>
  <div><h2>Pick a starting point</h2><p className="bs-helper">Reuse what already works, then make it your own. Choose one bot below, or start fresh.</p></div>
  <div className="bs-starters" aria-label="Starting points">
   <button type="button" className="bs-starter" aria-pressed={!source} disabled={busy} onClick={()=>choose(null)}><strong>Start blank</strong><span className="bs-helper">No preset purpose. Connect Telegram, then build anything.</span></button>
   {starters.length>6&&<label className="bs-label">Find a starting bot<input className="bs-input" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Search names and descriptions"/></label>}
   {shown.map(s=><button type="button" key={s.id} className="bs-starter" aria-pressed={source?.id===s.id} disabled={busy} onClick={()=>choose(s)}><span className="bs-starter-title"><strong>{s.name}</strong><span className="bs-badge">{s.version?`Published · v${s.version}`:'Unpublished draft'}</span></span><span className="bs-helper">{s.description}</span></button>)}
   {query&&!shown.length&&<p className="bs-helper">No matching bots. Try another search or start blank.</p>}
  </div>
  {source&&<div className="bs-audience" role="status"><strong>Based on {source.name}</strong><p className="bs-helper">Copies functionality, introduction{source.uses_model?' and AI instructions / model':''}. No users, notes, conversations, bot keys or service connections are copied. The original stays unchanged.</p><p className="bs-helper">{source.uses_model?`AI model: ${source.model}. `:''}{source.private_only?'This starting point is private-test-only. ':!source.version?'This is an unpublished draft, not a verified release. ':''}Connect your own services, then test in Telegram before sharing.</p></div>}
  {changed&&<p className="bs-error" role="alert">This starting point changed or was removed. Choose it again, or start blank.</p>}
  <label className="bs-label">New bot name<input className="bs-input" value={name} maxLength={80} onChange={e=>setName(e.target.value)} placeholder="Give your new bot a name" autoFocus disabled={busy}/></label>
  <button className="bs-btn bs-primary" disabled={busy||!name.trim()||!!changed}>{busy?'Creating…':source?'Create based on '+source.name:'Create blank bot'}</button>
  <p className="bs-helper">Next: connect a separate Telegram bot and customize in your private testing loop. Nothing is published automatically.</p>
 </form></section>
}
