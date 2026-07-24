import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Moneymoney 台股分析",
  description: "台股 K 線、MACD 訊號與條件選股工具",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-Hant">
      <body>{children}</body>
    </html>
  );
}
