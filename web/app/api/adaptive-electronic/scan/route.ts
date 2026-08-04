import { NextRequest, NextResponse } from "next/server";
import { verifyAdaptiveScannerToken } from "@/lib/private-site-auth";
import { buildAdaptiveElectronicScan } from "@/services/adaptive-electronic-service";

export const runtime="nodejs";
export const dynamic="force-dynamic";
export async function GET(request:NextRequest){
  if (!await verifyAdaptiveScannerToken(request.headers.get("x-adaptive-scanner-token"))) {
    return NextResponse.json({error:"掃描服務驗證失敗"},{status:401});
  }
  try{return NextResponse.json(await buildAdaptiveElectronicScan());}catch(error){console.error("adaptive-electronic scan",error);return NextResponse.json({error:"官方電子股與指定題材掃描資料暫時無法完整取得"},{status:503});}
}
