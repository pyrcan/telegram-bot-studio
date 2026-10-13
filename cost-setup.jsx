import React,{useEffect,useRef,useState} from 'react'

const own=(object,key)=>Object.prototype.hasOwnProperty.call(object,key)
const kinds=[['subscription','Shared subscription'],['tokens','Token rates'],['local','Local monthly pool'],['unknown','Unknown price']]
const settings=value=>({schema:2,currency:value.currency,monthly_hosting:value.monthly_hosting,hosting_basis:value.hosting_basis,default_profile:value.default_profile,profiles:value.profiles.map(p=>({...p})),assignments:{...value.assignments}})
const money=(value,currency)=>value==null?'Unknown':new Intl.NumberFormat(undefined,{style:'currency',currency,maximumFractionDigits:2}).format(value)

export default function CostSetup({initial,bots,history=[],save,reload,busy}){
 const [open,setOpen]=useState(false),[value,setValue]=useState(()=>settings(initial))
 const revision=useRef(initial.revision),dirty=useRef(false)
 const [working,setWorking]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('')
 useEffect(()=>{if(!dirty.current){setValue(settings(initial));revision.current=initial.revision}},[initial.revision])
 const edit=fn=>{dirty.current=true;setNotice('');setValue(fn)}
 const profileEdit=(id,key,next)=>edit(v=>({...v,profiles:v.profiles.map(p=>p.id===id?{...p,[key]:next}:p)}))
 const numeric=(label,current,change)=> <label className="bs-label">{label}<input className="bs-input" type="number" min="0" max="1000000000" step="any" value={current??''} placeholder="Unknown" onChange={e=>change(e.target.value===''?null:Number(e.target.value))}/></label>
 const options=()=>value.profiles.map(p=><option value={p.id} key={p.id}>{p.name||'Unnamed profile'}</option>)
 const remove=id=>edit(v=>({...v,profiles:v.profiles.filter(p=>p.id!==id),default_profile:v.default_profile===id?null:v.default_profile,assignments:Object.fromEntries(Object.entries(v.assignments).map(([bot,profile])=>[bot,profile===id?null:profile]))}))
 const accept=saved=>{setValue(settings(saved));revision.current=saved.revision;dirty.current=false;setError('')}
 const snapshots=history.filter(h=>h.revision!==initial.revision).slice().sort((a,b)=>b.effective-a.effective).slice(0,4)
 return <div className="bs-cost-setup">
  <div className="bs-row bs-cost-toolbar"><button className="bs-btn bs-primary" type="button" aria-expanded={open} aria-controls="bs-cost-form" onClick={()=>setOpen(v=>!v)}>Costs setup</button><span className="bs-helper">Owner-controlled accounting only</span></div>
  {open&&<div id="bs-cost-form">
   <p className="bs-helper">Set Studio’s hosting share and reusable billing profiles. Blank means unknown, not free. These settings estimate costs; they do not make payments, change bot behavior or switch providers/models. The current AI runner is OpenAI Codex only.</p>
   <p className="bs-audience bs-helper">Prefer chat? Ask your ordinary Möbius owner chat to edit costs for the exact bot, using its name and ID shown below. Review the profile and amount there before saving. No new login or embedded chat is needed. Enter amounts and labels only, never credentials.</p>
   <form onSubmit={async e=>{e.preventDefault();setWorking(true);setError('');setNotice('');try{accept(await save({...value,expected_revision:revision.current}));setNotice('Cost assumptions saved. Bot runtime is unchanged.')}catch(e){setError(e.message)}finally{setWorking(false)}}}>
    {error&&<p className="bs-error" role="alert">{error} Your edits are kept. Reload saved assumptions to review the latest settings before trying again.</p>}
    {notice&&<p className="bs-notice" role="status">{notice}</p>}
    <fieldset className="bs-fieldset bs-form" disabled={working||busy}>
     <div className="bs-cost-grid">
      <label className="bs-label">Currency<select className="bs-input" value={value.currency} onChange={e=>edit(v=>({...v,currency:e.target.value}))}>{['USD','GBP','EUR'].map(c=><option key={c}>{c}</option>)}</select></label>
      {numeric('Monthly hosting allocation',value.monthly_hosting,next=>edit(v=>({...v,monthly_hosting:next})))}
      <label className="bs-label">Hosting basis<select className="bs-input" value={value.hosting_basis} onChange={e=>edit(v=>({...v,hosting_basis:e.target.value}))}><option value="allocation">Studio allocation</option><option value="ceiling">Budget ceiling</option></select></label>
     </div>
     <p className="bs-helper">Currency is shared by these assumptions. Changing it does not convert historical estimates or invoices. A ceiling is a conservative assumption, not measured spend or an enforced spending cap.</p>
     <div><h3>Billing profiles</h3><p className="bs-helper">Use one profile per shared subscription or local monthly pool, not a copy for every bot. Each monthly pool is counted once overall and divided among its assigned bots. Unassigned paid pools still count in the overall budget. Token profiles contain rates only.</p></div>
     {!value.profiles.length&&<p className="bs-helper">No billing profiles yet. AI costs stay unknown until you add and assign a priced profile.</p>}
     {value.profiles.map((profile,index)=><section className="bs-cost-profile" key={profile.id} aria-label={`Billing profile ${index+1}`}>
      <div className="bs-card-top"><h4>Profile {index+1}</h4><button type="button" className="bs-btn" onClick={()=>remove(profile.id)}>Remove profile</button></div>
      <div className="bs-cost-grid">
       <label className="bs-label">Profile name<input className="bs-input" required maxLength="80" value={profile.name} onChange={e=>profileEdit(profile.id,'name',e.target.value)}/></label>
       <label className="bs-label">Accounting provider label<input className="bs-input" maxLength="80" value={profile.provider} placeholder="Optional label" onChange={e=>profileEdit(profile.id,'provider',e.target.value)}/></label>
       <label className="bs-label">Model label / exact rate match<input className="bs-input" maxLength="160" value={profile.model} placeholder="Optional exact model" onChange={e=>profileEdit(profile.id,'model',e.target.value)}/></label>
       <label className="bs-label">Cost type<select className="bs-input" value={profile.kind} onChange={e=>profileEdit(profile.id,'kind',e.target.value)}>{kinds.map(([id,label])=><option value={id} key={id}>{label}</option>)}</select></label>
       {['subscription','local'].includes(profile.kind)&&numeric('Shared monthly cost',profile.monthly_cost,next=>profileEdit(profile.id,'monthly_cost',next))}
       {profile.kind==='tokens'&&<>{numeric('Uncached input · per million tokens',profile.input_per_million,next=>profileEdit(profile.id,'input_per_million',next))}{numeric('Cached input · per million tokens',profile.cached_per_million,next=>profileEdit(profile.id,'cached_per_million',next))}{numeric('Output · per million tokens',profile.output_per_million,next=>profileEdit(profile.id,'output_per_million',next))}</>}
      </div>
      {profile.kind==='tokens'&&<p className="bs-helper">Use verified rates for the actual model. An optional model label must match exactly or token cost is unknown. Rates are pinned to new measured calls, never recalculated over old calls.</p>}
      {profile.kind==='unknown'&&<p className="bs-helper">No price is assumed for this profile. Unknown is not zero.</p>}
     </section>)}
     <button className="bs-btn" type="button" disabled={value.profiles.length>=32} onClick={()=>edit(v=>({...v,profiles:[...v.profiles,{id:'profile-'+crypto.randomUUID(),name:'New profile',provider:'',model:'',kind:'unknown',monthly_cost:null,input_per_million:null,cached_per_million:null,output_per_million:null}]}))}>Add billing profile</button>
     <label className="bs-label">Default AI profile<select className="bs-input" value={value.default_profile??''} onChange={e=>edit(v=>({...v,default_profile:e.target.value||null}))}><option value="">No default profile</option>{options()}</select></label>
     <div><h3>Per-bot cost profiles</h3><p className="bs-helper">Use default follows the default for AI-enabled bots. No profile explicitly opts out. Changing accounting labels does not switch the actual runner or model.</p></div>
     {bots.map(bot=>{
      const chosen=own(value.assignments,bot.id)?value.assignments[bot.id]:bot.uses_model?value.default_profile:null
      const assigned=value.profiles.find(p=>p.id===chosen)
      const mismatch=assigned?.kind==='tokens'&&assigned.model&&assigned.model!==bot.actual_model
      return <div className="bs-cost-assignment" key={bot.id}>
       <label className="bs-label">{bot.name}<select className="bs-input" aria-label={`Cost profile for ${bot.name}`} value={own(value.assignments,bot.id)?value.assignments[bot.id]??'__none':'__default'} onChange={e=>edit(v=>{const assignments={...v.assignments};if(e.target.value==='__default')delete assignments[bot.id];else assignments[bot.id]=e.target.value==='__none'?null:e.target.value;return {...v,assignments}})}><option value="__default">Use default</option><option value="__none">No profile</option>{options()}</select></label>
       <p className="bs-helper">Bot ID: {bot.id} · Actual runner: {bot.runner_provider||'No AI runner'} · Actual model: {bot.actual_model||'None'}{!bot.uses_model?' · No AI use; default does not apply.':` · Accounting: ${assigned?.name||'No profile'}`}</p>
       {bot.uses_model&&mismatch&&<p className="bs-error">Model mismatch: this profile’s token rates do not apply to {bot.actual_model||'an unknown model'}. New token costs will be unknown.</p>}
      </div>
     })}
     <p className="bs-helper">Removing a profile changes its explicit bot assignments to No profile and clears it as the default; past cost estimates remain pinned.</p>
     <div className="bs-row"><button className="bs-btn" type="button" onClick={async()=>{setWorking(true);setError('');setNotice('');try{accept(await reload())}catch(e){setError(e.message)}finally{setWorking(false)}}}>Reload saved assumptions</button><button className="bs-btn bs-primary">{working?'Saving…':'Save cost assumptions'}</button></div>
    </fieldset>
   </form>
   {snapshots.length>0&&<div className="bs-cost-history"><h3>Previous saved assumptions</h3><p className="bs-helper">Immutable settings snapshots, not historical invoices. The initial migrated timestamp is when it was captured, not its original save date. Past call estimates keep their original rates and currency.</p><ul>{snapshots.map(h=><li key={h.revision}><strong>{h.settings.currency}</strong> · {new Date(h.effective*1000).toLocaleString()}<span className="bs-helper">Hosting {money(h.settings.monthly_hosting,h.settings.currency)} · {h.settings.profiles.length} profiles</span></li>)}</ul></div>}
  </div>}
 </div>
}
