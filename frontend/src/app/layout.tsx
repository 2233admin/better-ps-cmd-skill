import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "A股日内量化交易终端",
  description: "基于通达信的AI全自动化交易平台",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">{children}</body>
    </html>
  );
}
