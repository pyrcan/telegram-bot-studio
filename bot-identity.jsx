import React,{useState,useRef,useEffect} from 'react'
export default function BotIdentity({bot,save,busy,customize,reload}){
 const [value,setValue]=useState(bot.display||{icon:bot.name.slice(0,2).toUpperCase(),purpose:bot.description||'Describe its purpose',revision:'0'})
 const dirty=useRef(false)
 useEffect(()=>{if(!dirty.current&&bot.display)setValue(bot.display)},[bot.display?.revision])
 const edit=(key,next)=>{dirty.current=true;setValue(v=>({...v,[key]:next}))}
 return <section className="bs-card bs-identity">
  <div className="bs-customize-lead"><div><h2>Customize your bot</h2><p className="bs-helper">Describe your idea in its editing chat. Test and publish remain separate.</p></div><button className="bs-btn bs-primary" disabled={busy} onClick={customize}>Customize in chat</button></div>
  <details className="bs-advanced"><summary>Appearance & purpose · edit</summary>
  <div className="bs-card-top"><div className="bs-mark" aria-hidden="true">{value.icon}</div><div><h3>Your Studio identity</h3><p className="bs-helper">Your Studio labels—not Telegram’s profile or bot instructions.</p></div></div>
  <form className="bs-form" onSubmit={async e=>{e.preventDefault();const result=await save(value);if(result){dirty.current=false;setValue(result)}}}>
   <label className="bs-label">Icon · emoji or initials<input className="bs-input" value={value.icon} maxLength={32} onChange={e=>edit('icon',e.target.value)}/></label>
   <div className="bs-icon-choices" aria-label="Suggested icons">{['💬','📒','🎬','🎵','🧭','✨'].map(icon=><button type="button" className="bs-btn" key={icon} aria-label={`Use ${icon} icon`} onClick={()=>edit('icon',icon)}>{icon}</button>)}</div>
   <label className="bs-label">Purpose label<input className="bs-input" value={value.purpose} maxLength={160} onChange={e=>edit('purpose',e.target.value)}/></label>
   <button type="button" className="bs-btn" disabled={busy} onClick={async()=>{const result=await reload();if(result){dirty.current=false;setValue(result)}}}>Reload appearance</button>
   <button className="bs-btn" disabled={busy||!value.icon.trim()||!value.purpose.trim()}>Save appearance</button>
  </form>

  </details>
 </section>
}
