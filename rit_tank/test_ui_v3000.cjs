// Execute the actual reset dialog code with a mocked DOM/API.
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(__dirname+'/app.py','utf8'),elements=new Map(),calls=[];
const $=id=>{if(!elements.has(id))elements.set(id,{value:'',disabled:false,hidden:false,textContent:'',show:false,focus(){},classList:{contains(){return elements.get(id).show}}});return elements.get(id)};
let reloads=0,fail=false,release=null;
const ctx=vm.createContext({$,fmt:n=>String(n),location:{reload(){reloads++}},
 openModal(id){$(id).show=true},closeModal(id){$(id).show=false},
 api:async(path,opts)=>{calls.push({path,opts});if(path.endsWith('reset-token'))return {token:'test-token'};if(fail)throw Error('Wissen mislukt');if(release)return new Promise(r=>release=r);return {ok:true}}
});
const start=source.indexOf("let ADMIN_RESET_TOKEN="),end=source.indexOf('async function saveSettings()',start);
vm.runInContext(source.slice(start,end),ctx);
const run=code=>vm.runInContext(code,ctx);
(async()=>{
 await run('openAdministrationReset()');assert.equal($('resetFinalButton').disabled,true);assert.equal($('resetOdometerStep').hidden,true);
 for(const value of ['reset','RESET ',' RESET']){$('resetConfirmation').value=value;run('updateAdministrationReset()');assert.equal($('resetFinalButton').disabled,true)}
 $('resetConfirmation').value='RESET';run('updateAdministrationReset()');assert.equal($('resetOdometerStep').hidden,false);assert.equal($('resetFinalButton').disabled,true);
 for(const value of ['', '-1','NaN','1e5','1000000','64.60.3','64603,5','64.603,5']){$('resetOdometer').value=value;run('updateAdministrationReset()');assert.equal($('resetFinalButton').disabled,true)}
 $('resetOdometer').value='64.603';run('updateAdministrationReset()');assert.equal($('resetFinalButton').disabled,false);assert.match($('resetBaselinePreview').textContent,/64603/);assert.match($('resetBaselinePreview').textContent,/0 km/);
 run('cancelAdministrationReset()');await run('submitAdministrationReset()');assert.equal(calls.filter(x=>x.opts).length,0);assert.equal($('administrationResetModal').show,false);
 await run('openAdministrationReset()');$('resetConfirmation').value='RESET';$('resetOdometer').value='64603';fail=true;
 await run('submitAdministrationReset()');assert.match($('resetError').textContent,/Wissen mislukt/);assert.equal(reloads,0);
 fail=false;release=true;const first=run('submitAdministrationReset()');assert.equal($('resetFinalButton').disabled,true);
 const count=calls.length;await run('submitAdministrationReset()');assert.equal(calls.length,count);release({ok:true});await first;assert.equal(reloads,1);
 const post=calls.find(x=>x.opts);assert.equal(post.opts.method,'POST');assert.equal(post.opts.headers['X-Reset-Token'],'test-token');assert.equal(JSON.parse(post.opts.body).confirmation,'RESET');
 assert.match(source,/role="dialog" aria-modal="true" aria-labelledby="resetTitle"/);
 console.log('Reset UI: confirmation, baseline, cancel, errors, CSRF, duplicate click and reload passed');
})().catch(e=>{console.error(e);process.exitCode=1});
