import React,{useEffect,useState,useRef} from 'react'
import CostSetup from './cost-setup.jsx'

const n=value=>new Intl.NumberFormat(undefined,{maximumFractionDigits:1}).format(value||0)
const seconds=ms=>`${n(ms/1000)} s`
const money=(value,currency)=>value===null||value===undefined?'Unknown':new Intl.NumberFormat(undefined,{style:'currency',currency,maximumFractionDigits:4}).format(value)
const add=(a,b)=>Object.fromEntries(Object.keys(a).map(key=>[key,a[key]+b[key]]))

function Stats({items}){
 return <dl className="bs-dash-stats">{items.map(([label,value,hint])=><div key={label}><dt>{label}</dt><dd>{value}</dd>{hint&&<small>{hint}</small>}</div>)}</dl>
}
function Usage({live,test,bots}){
 const [bucket,setBucket]=useState('live')
 const usage=bucket==='live'?live:test
 return <div>
  <div className="bs-row bs-spaced" aria-label="Usage environment"><button className="bs-btn" aria-pressed={bucket==='live'} onClick={()=>setBucket('live')}>Published use</button><button className="bs-btn" aria-pressed={bucket==='test'} onClick={()=>setBucket('test')}>Private tests</button></div>
  <Stats items={[
   ['Interactions',n(usage.interactions),'Admitted messages and button taps'],
   ['Tasks processed',n(usage.tasks),`${n(usage.task_errors)} errors · ${n(usage.task_cancelled)} cancelled`],
   ['AI calls',n(usage.ai_calls),`${n(usage.metered_ai_calls)} with token measurements`],
   ['Telegram operations',n(usage.telegram_confirmed),`${n(usage.telegram_failed)} failed or uncertain`],
  ]}/>
  <details className="bs-advanced"><summary>Processing & resources</summary>
   <Stats items={[
    ['Average processing',usage.tasks?seconds(usage.process_ms/usage.tasks):'No sample','Wall time, includes waiting and child tools'],
    ['Average queue wait',usage.tasks?seconds(usage.queue_ms/usage.tasks):'No sample'],
    ['Worker CPU',seconds(usage.worker_cpu_ms),'Excludes model provider and child processes'],
    ['Input / output tokens',`${n(usage.input_tokens)} / ${n(usage.output_tokens)}`,`${n(usage.cached_input_tokens)} cached input · metered calls only`],
    ['Processing total',seconds(usage.process_ms),'Not a hosting invoice'],
   ]}/>
  </details>
 </div>
}
export default function Dashboard({appId,token,initialBot=null,onManage}){
 const [data,setData]=useState(null),[error,setError]=useState(''),[selected,setSelected]=useState(initialBot||'all')
 const [loading,setLoading]=useState(false),readSequence=useRef(0)
 const request=async(body)=>{const path=body?'owner/cost-assumptions':'owner/dashboard';const response=await fetch(`/api/apps/${appId}/service/${path}`,{method:body?'POST':'GET',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});const result=await response.json();if(!response.ok)throw Error(result.error||result.detail||'Could not load dashboard');return result}
 const refresh=async()=>{const sequence=++readSequence.current;setLoading(true);setError('');try{const next=await request();if(sequence===readSequence.current)setData(next)}catch(e){if(sequence===readSequence.current)setError(e.message)}finally{setLoading(false)}}
 useEffect(()=>{let current=true;setError('');const load=()=>{const sequence=++readSequence.current;request().then(v=>{if(current&&sequence===readSequence.current){setData(v);setError('')}}).catch(e=>{if(current&&sequence===readSequence.current)setError(e.message)})};load();const unsubscribe=window.mobius?.storage?.subscribe('revision.json',load);return()=>{current=false;readSequence.current++;unsubscribe?.()}},[appId,token])
 if(!data)return <section className="bs-card">{error?<><p className="bs-error" role="alert">{error}</p><button className="bs-btn" onClick={refresh}>Try again</button></>:<p>Loading aggregate dashboard…</p>}</section>
 const currency=data.assumptions.currency
 const bots=selected==='all'?data.bots:data.bots.filter(b=>b.id===selected)
 const fixedCost=key=>selected==='all'?data.cost_summary?.[key]:bots[0]?.cost[key]
 const live=selected==='all'?data.usage.live:bots[0]?.usage.live||data.usage.live
 const test=selected==='all'?data.usage.test:bots[0]?.usage.test||data.usage.test
 const users=selected==='all'?data.users:bots[0]?.users||data.users
 const usage=add(live,test),priced=bots.reduce((total,b)=>total+(b.cost.priced_ai_calls||0),0)
 const counter=key=>bots.reduce((total,b)=>total+(b.cost[key]||0),0)
 const aiEstimate=priced?bots.reduce((total,b)=>total+(b.cost.metered_ai_estimate??0),0):bots.length&&bots.every(b=>b.cost.metered_ai_estimate===0)?0:null
 return <div className="bs-dashboard">
  {error&&<p className="bs-error" role="alert">{error}</p>}
  <section className="bs-card">
   <div className="bs-card-top"><div><h2>Usage & costs</h2><p className="bs-helper">This month · {data.period.start} onwards · UTC</p></div><button className="bs-btn" disabled={loading} onClick={refresh}>{loading?'Refreshing…':'Refresh'}</button></div>
   <p className="bs-audience bs-helper">New activity tracking: {data.bots.length?new Date(Math.min(...data.bots.map(b=>b.since))*1000).toLocaleString():'not started'}. Earlier usage is not imported; zero here means no activity recorded since tracking began.</p>
   <label className="bs-label">Bot<select className="bs-input" value={selected} onChange={e=>setSelected(e.target.value)}><option value="all">All current bots</option>{data.bots.map(b=><option value={b.id} key={b.id}>{b.name}</option>)}</select></label>
   <Stats items={[['Approved users',n(users.allowed),'Bot memberships · excludes owner'],['Joined users',n(users.joined),'Approved accounts that opened a chat'],['Requests waiting',n(users.pending),'Review in Users']]}/>
   <Usage live={live} test={test} bots={bots}/>
  </section>
  <section className="bs-card"><h2>Cost picture</h2><p className="bs-helper">Current monthly budgets use current assumptions, not a historical invoice. Recorded AI use below keeps historical rates and includes private testing.</p>
   <CostSetup initial={data.assumptions} bots={data.bots} history={data.cost_history} busy={loading} save={async body=>{const result=await request(body);await refresh();return result}} reload={async()=>{const result=await request();setData(result);return result.assumptions}}/>
   <Stats items={[
    ['Monthly budget estimate',money(fixedCost('monthly_budget_estimate'),currency),data.assumptions.hosting_basis==='ceiling'?'Conservative full-cost assumption · not a spending cap':'Current assumptions · hosting + shared monthly AI pools'],
    ['Hosting / month',money(fixedCost('hosting_monthly_estimate'),currency),data.assumptions.hosting_basis==='ceiling'?'Current planning ceiling · not measured spend':'Current full-month Studio allocation'],
    ['AI budget / month',money(fixedCost('ai_monthly_allocation_estimate'),currency),'Current shared pools · each counted once overall'],
    ['Metered AI use',money(aiEstimate,currency),'Pinned historical token estimates · current currency only'],
    ['Average metered AI call',money(aiEstimate!==null&&priced?aiEstimate/priced:null,currency),`${n(priced)} priced calls · excludes unknown and other-currency calls`],
   ]}/>
   {selected==='all'&&data.cost_summary?.variable_ai_cost_unknown&&<p className="bs-helper">The monthly total is unknown because some bots use variable or unpriced AI. Known shared monthly pools are shown separately; token use is not a full-month forecast.</p>}
   {selected==='all'&&<p className="bs-helper">Unassigned AI pools / month: {money(data.cost_summary?.unallocated_ai_monthly_estimate,currency)}. Included once in the overall budget above, even with no assigned bots.</p>}
   {counter('unknown_ai_calls')>0&&<p className="bs-error">{n(counter('unknown_ai_calls'))} AI calls have no pinned applicable price, including older calls. Their cost is unknown, not zero.</p>}
   {counter('unmetered_ai_calls')>0&&<p className="bs-error">{n(counter('unmetered_ai_calls'))} AI calls have no token measurement. Their cost is unknown, not zero.</p>}
   {counter('other_currency_calls')>0&&<p className="bs-error">{n(counter('other_currency_calls'))} historical AI calls use another currency and are not included above. No automatic FX conversion.</p>}
   <p className="bs-helper">{data.allocation}</p>
   {bots.some(b=>b.cost.estimate_history?.length)&&<details className="bs-advanced"><summary>Preserved usage estimates</summary><p className="bs-helper">This month’s recorded estimates in their original currencies. No conversion or repricing. These are not invoices.</p>{bots.map(b=>(b.cost.estimate_history||[]).map((row,index)=><div className="bs-cost-assignment" key={b.id+index}><strong>{b.name} · {row.model||'Unknown model'}</strong><p className="bs-helper">{row.mode==='test'?'Private test':'Published use'} · {money(row.metered_ai_estimate,row.currency)} · {n(row.priced_ai_calls)} priced / {n(row.unknown_ai_calls)} unknown calls · Settings {row.revision}</p></div>))}</details>}
  </section>
  {bots.length>0&&<section className="bs-card"><h2>Per bot</h2><div className="bs-dash-bots">{bots.map(b=><article key={b.id}>
   <div className="bs-card-top"><div className="bs-mark" aria-hidden="true">{b.display.icon}</div><div className="bs-bot-name"><h3>{b.name}</h3><p className="bs-helper">{b.display.purpose}</p></div><button className="bs-btn" onClick={()=>onManage(b.id)}>Manage</button></div>
   <p>{n(b.users.allowed)} approved · {n(b.usage.live.interactions)} published interactions · {n(b.usage.test.interactions)} test interactions</p>
   <p className="bs-helper">Actual runner: {b.runner_provider||'No AI runner'} · Actual model: {b.actual_model||'None'} · Accounting profile: {b.cost.profile_name||'No profile'}. Accounting labels do not switch runtime.</p>
   <p className="bs-helper">Hosting/month {money(b.cost.hosting_monthly_estimate,currency)} · AI budget/month {money(b.cost.ai_monthly_allocation_estimate,currency)} · metered AI {money(b.cost.metered_ai_estimate,currency)}</p>
   <p className="bs-helper">Tracking since {new Date(b.since*1000).toLocaleString()}{b.incomplete?' · Some measurements could not be recorded.':''}</p>
  </article>)}</div></section>}
  <section className="bs-card"><h2>Daily activity</h2><p className="bs-helper">Admitted interactions only; no person-level breakdown.</p><div className="bs-dash-days">{(()=>{const days=new Map();bots.forEach(b=>b.daily.forEach(d=>{const old=days.get(d.day)||{day:d.day,live_interactions:0,test_interactions:0};old.live_interactions+=d.live_interactions;old.test_interactions+=d.test_interactions;days.set(d.day,old)}));return days.size?[...days.values()].sort((a,b)=>b.day.localeCompare(a.day)).map(d=><div key={d.day}><strong>{d.day}</strong><span>{n(d.live_interactions)} published · {n(d.test_interactions)} test</span></div>):<p className="bs-helper">No activity measured yet. Earlier conversations are not imported.</p>})()}</div></section>
  <p className="bs-helper bs-footer">{data.disclosure}</p>
 </div>
}
