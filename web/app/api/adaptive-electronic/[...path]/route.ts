import { NextRequest, NextResponse } from "next/server";
import { backendJson, BackendUnavailableError } from "@/services/backend-client";
import { getUserId } from "@/lib/portfolio-api";

export const runtime="nodejs";
export const dynamic="force-dynamic";

async function proxy(request:NextRequest,context:{params:Promise<{path:string[]}>}){
  const {path}=await context.params;
  const target=`/adaptive-electronic/${path.join("/")}${request.nextUrl.search}`;
  const headers:Record<string,string>={};
  const userId=getUserId(request);if(userId)headers["x-user-id"]=userId;
  const contentType=request.headers.get("content-type");if(contentType)headers["content-type"]=contentType;
  try{
    const body=["GET","HEAD"].includes(request.method)?undefined:await request.text();
    const timeoutMs=["GET","HEAD"].includes(request.method)?12_000:30_000;
    const payload=await backendJson<unknown>(target,{method:request.method,headers,body},timeoutMs);
    return NextResponse.json(payload);
  }catch(error){const message=error instanceof BackendUnavailableError?"超強 AI 後端暫時無法連線":error instanceof Error?error.message:"請稍後再試";return NextResponse.json({error:message},{status:503});}
}
export const GET=proxy;export const POST=proxy;export const PUT=proxy;export const DELETE=proxy;
