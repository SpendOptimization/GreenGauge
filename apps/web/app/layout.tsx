import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "GreenGauge · Model routing",
  description: "Route coding issues using observed cost per CI-green issue.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
