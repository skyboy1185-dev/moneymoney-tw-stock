import type { Metadata } from "next";
import "./globals.css";
import { LegalTermsGate } from "@/components/LegalTermsGate";
import { DayTradingRobotNotifier } from "@/components/DayTradingRobotNotifier";

export const metadata: Metadata = {
  title: "Moneymoney 台股分析",
  description: "台股 K 線、MACD 訊號與條件選股工具",
  robots: { index: false, follow: false, noarchive: true },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-Hant">
      <body><DayTradingRobotNotifier />{children}<LegalTermsGate /></body>
    </html>
  );
}
