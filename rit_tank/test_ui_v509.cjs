// Original selector cases with the release-27 asynchronous validation gate.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync(__dirname+'/app.py','utf8');
const elements={};const get=id=>elements[id]??=({value:'',hidden:false,textContent:'',innerHTML:''});
let opened,closed,url;
const ctx={Date,Number,setTimeout,clearTimeout,$:get,esc:String,fmt:String,openModal:id=>opened=id,closeModal:id=>closed=id,openPdfExport:u=>url=u,
 api:async()=>({status:'ok',period:{label:'Test'},summary:{row_count:1,business_km:10,reimbursement:'2.50',errors:0,warnings:0,clean_rows:1},issues:[]})};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('let REPORT_CHECK='),source.indexOf('function closePdfPreview')),ctx);
(async()=>{
 ctx.openPdfSelector();assert.equal(opened,'pdfPeriodModal');assert.equal(get('pdfRange').value,'month');
 get('pdfYear').value='2026';get('pdfMonth').value='9';await ctx.runReportValidation();ctx.confirmPdfPeriod();assert.equal(url,'api/business.pdf?period=month&year=2026&month=9');
 get('pdfRange').value='year';get('pdfYear').value='2027';await ctx.runReportValidation();ctx.confirmPdfPeriod();assert.equal(url,'api/business.pdf?period=year&year=2027');
 url=null;get('pdfYear').value='oops';await ctx.runReportValidation();ctx.confirmPdfPeriod();assert.equal(url,null);assert(get('pdfPeriodError').textContent);
 assert(source.includes('class="trip-primary" style="margin-bottom:10px" onclick="openPdfSelector()"'));
 console.log('PASS: PDF selector, selected month/year URLs, invalid year, full-width dashboard button.');
})().catch(e=>{console.error(e);process.exitCode=1});
