import { NextRequest, NextResponse } from "next/server";
import { backendJson } from "@/services/backend-client";
import { resolveOfficialStock } from "@/services/market-data/stock-directory";
import { getOfficialHistory } from "@/services/market-data/official-history-provider";

export const runtime="nodejs";export const dynamic="force-dynamic";
export async function POST(request:NextRequest){try{const body=await request.json() as {stockCode?:string;strategyType?:string;years?:number};const stock=await resolveOfficialStock(body.stockCode??"");if(!stock)return NextResponse.json({error:"找不到股票"},{status:404});const years=[1,3,5].includes(body.years??0)?body.years!:1;const history=await getOfficialHistory(stock);const cutoff=new Date();cutoff.setUTCFullYear(cutoff.getUTCFullYear()-years);const prices=history.filter((row)=>row.date>=cutoff.toISOString().slice(0,10));const result=await backendJson("/adaptive-electronic/backtest",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({stock_code:stock.symbol,stock_name:stock.name,strategy_type:body.strategyType??"BREAKOUT",years,prices,benchmark_prices:[]})},30_000);return NextResponse.json(result);}catch(error){return NextResponse.json({error:error instanceof Error?error.message:"回測失敗"},{status:503});}}
