import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "GreenGauge · Model routing",
  description: "Route every coding issue to the model with the lowest expected total cost.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

