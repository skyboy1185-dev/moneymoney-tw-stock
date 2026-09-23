import { NextRequest, NextResponse } from "next/server";
import { getBackendBaseUrl } from "@/lib/runtime-config";
export const runtime="nodejs";export const dynamic="force-dynamic";
async function proxy(request:NextRequest,context:{params:Promise<{path:string[]}>}){
  const {path}=await context.params;const target=new URL(`/api/v1/prepost-analysis/${path.join("/")}`,getBackendBaseUrl());target.search=request.nextUrl.search;
  const headers=new Headers({Accept:request.headers.get("accept")??"application/json"});const user=request.headers.get("x-user-id");if(user)headers.set("x-user-id",user);const type=request.headers.get("content-type");if(type)headers.set("content-type",type);
  try{const response=await fetch(target,{method:request.method,headers,body:["GET","HEAD"].includes(request.method)?undefined:await request.arrayBuffer(),cache:"no-store",signal:AbortSignal.timeout(190_000)});return new NextResponse(response.body,{status:response.status,headers:{"content-type":response.headers.get("content-type")??"application/json","content-disposition":response.headers.get("content-disposition")??""}});}catch{return NextResponse.json({error:"盤前盤後分析服務暫時無法連線"},{status:502});}
}
export const GET=proxy;export const POST=proxy;export const PATCH=proxy;
