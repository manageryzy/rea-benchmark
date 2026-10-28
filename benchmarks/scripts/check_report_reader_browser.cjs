#!/usr/bin/env node
/* All public pages, three locales, offline. No report rebuild or provider calls. */
const fs=require('node:fs');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const assert=require('node:assert/strict');
const args=process.argv.slice(2);
const option=(name,fallback)=>args.includes(name)?args[args.indexOf(name)+1]:fallback;
const site=path.resolve(option('--site','benchmarks/site'));
const output=path.resolve(option('--output','.cache/benchmark-runtime/report-reader-browser'));
let playwright;
for(const name of [process.env.PLAYWRIGHT_MODULE,'playwright','/mnt/c/Users/mana/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'].filter(Boolean)){
 try{playwright=require(name);break;}catch(e){if(e.code!=='MODULE_NOT_FOUND')throw e;}
}
if(!playwright)throw Error('Playwright required for offline browser review');
const files=[];
function walk(dir){for(const entry of fs.readdirSync(dir,{withFileTypes:true})){
 const full=path.join(dir,entry.name);if(entry.isDirectory())walk(full);else if(entry.name.endsWith('.html'))files.push(path.relative(site,full));
}}
walk(site);files.sort();
const main=['index.html','comparison.html','dimensions.html','cases/index.html','capabilities.html','capability-inventory.html','costs.html','coverage.html','compilers.html','methodology.html','history/index.html'];
(async()=>{
 fs.mkdirSync(output,{recursive:true});
 const browser=await playwright.chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE||'/home/mana/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome',args:['--no-sandbox']});
 const failures=[],summaries=[],screens=[];let requests=0,checked=0;
 try{
 await Promise.all(['en','zh-CN','ja'].map(async language=>{
  const context=await browser.newContext({viewport:{width:1440,height:1000}});
  await context.route(/https?:\/\//,r=>{requests++;return r.abort();});
  let cursor=0;
  await Promise.all([0,1].map(async()=>{
   const page=await context.newPage();page.setDefaultTimeout(12000);
   page.on('pageerror',e=>failures.push({language,url:page.url(),error:e.message}));
   while(cursor<files.length){
    const file=files[cursor++];
    try{
     await page.goto(pathToFileURL(path.join(site,file)).href+'?lang='+language,{waitUntil:'load'});
     await page.waitForFunction(()=>window.REA_I18N&&document.documentElement.dataset.i18nWarnings!==undefined);
     const status=await page.evaluate(()=>{
      const collapsed=e=>{for(let p=e.parentElement;p;p=p.parentElement)if(p.tagName==='DETAILS'&&!p.open)return true;return false;};
      const main=document.querySelector('main');
      const figures=[...main.querySelectorAll('figure')].filter(e=>!e.closest('[hidden]'));
      return {language:document.documentElement.lang,warnings:window.REA_I18N.warnings,nav:document.querySelectorAll('.reader-navigation a').length,
       chars:main.innerText.length,overflow:document.documentElement.scrollWidth>innerWidth+2,collapsedFigures:figures.filter(collapsed).length,
       group:document.querySelector('[data-cohort-selection]')?.value};
     });
     assert.equal(status.language,language);assert.equal(status.nav,6);assert(status.chars>15);
     assert.deepEqual(status.warnings,[]);assert.equal(status.overflow,false);assert.equal(status.collapsedFigures,0);
     if(status.group)assert.equal(status.group,'all-models');
     checked++;
     if(main.includes(file)){
      summaries.push({file,language,...status});
      await page.evaluate(()=>document.querySelectorAll('main details').forEach(d=>d.open=true));
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false);
      if(['index.html','comparison.html','dimensions.html','capabilities.html','costs.html'].includes(file)){
       const image=language+'-'+file.replace('.html','')+'-desktop.png';
       await page.screenshot({path:path.join(output,image),fullPage:false});screens.push(image);
      }
      await page.setViewportSize({width:390,height:844});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false,file+': mobile expanded');
      if(['index.html','dimensions.html'].includes(file)){
       const image=language+'-'+file.replace('.html','')+'-mobile.png';await page.screenshot({path:path.join(output,image)});screens.push(image);
      }
      await page.setViewportSize({width:1440,height:1000});
     }
    }catch(e){failures.push({file,language,error:e.message});}
   }
   await page.close();
  }));
  await context.close();
 }));
 assert.equal(requests,0,'Unexpected external browser request');
 }finally{
 const result={pages:files.length,locales:3,checked,externalRequests:requests,browser:browser.version(),failures,summaries,screens};
 fs.writeFileSync(path.join(output,'result.json'),JSON.stringify(result,null,2));
 console.log(JSON.stringify({pages:files.length,checked,failures:failures.length,externalRequests:requests,output}));
 if(failures.length){console.log(JSON.stringify(failures.slice(0,8),null,2));process.exitCode=1;}
 await browser.close();
 }
})().catch(e=>{console.error(e);process.exitCode=1;});
