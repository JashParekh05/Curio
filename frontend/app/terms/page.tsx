import type { Metadata } from "next";
import LegalLayout, { LegalSection } from "@/components/LegalLayout";

export const metadata: Metadata = {
  title: "Terms of Service · Curio",
  description: "The terms governing your use of Curio.",
};

// Generated starting draft, review with counsel before relying on it. Update
// the entity name, contact addresses, and governing-law section to match your
// real business details and jurisdiction.
export default function TermsPage() {
  return (
    <LegalLayout title="Terms of Service" lastUpdated="June 20, 2026">
      <p>
        Welcome to Curio. These Terms of Service (&quot;Terms&quot;) form a
        binding agreement between you and Curio (&quot;Curio,&quot;
        &quot;we,&quot; &quot;us,&quot; or &quot;our&quot;) and govern your
        access to and use of the Curio website, applications, and services
        (collectively, the &quot;Service&quot;). By creating an account, signing
        in, or otherwise using the Service, you agree to these Terms. If you do
        not agree, do not use the Service.
      </p>

      <LegalSection heading="1. Eligibility & accounts">
        <p>
          You must be at least 13 years old to use the Service. If you are under
          the age of majority where you live, you may use the Service only with
          the involvement and consent of a parent or legal guardian. You are
          responsible for the activity that occurs under your account and for
          keeping your login credentials secure. Notify us promptly of any
          unauthorized use.
        </p>
      </LegalSection>

      <LegalSection heading="2. The Service">
        <p>
          Curio helps you learn by assembling short educational video segments
          into topic-based feeds. Videos play through embedded players provided
          by third-party platforms (such as YouTube). Curio does not host,
          store, download, or rebroadcast the underlying video or audio media.
          We store only references to publicly available content, a source link
          plus start and end timestamps, together with metadata we derive to
          organize and rank the feed. We may add, change, or remove features at
          any time.
        </p>
      </LegalSection>

      <LegalSection heading="3. Third-party content & platforms">
        <p>
          Content surfaced in Curio originates from third-party platforms and
          remains subject to those platforms&apos; own terms of service and
          privacy policies. When a clip plays, it does so through the source
          platform&apos;s official embed player, and your interaction with that
          player is governed by the source platform. Curio does not own,
          control, or endorse third-party content and is not responsible for its
          accuracy, legality, or availability. A clip may become unavailable at
          any time if the source platform removes or restricts it.
        </p>
      </LegalSection>

      <LegalSection heading="4. Acceptable use">
        <p>You agree not to:</p>
        <ul className="list-disc pl-5 space-y-1">
          <li>use the Service for any unlawful purpose or in violation of these Terms;</li>
          <li>infringe the intellectual property or other rights of any party;</li>
          <li>
            submit, link to, or attempt to surface content you do not have the
            right to share, or that is unlawful, infringing, hateful, harassing,
            sexually explicit, or otherwise harmful;
          </li>
          <li>
            scrape, crawl, download, or re-host media, or circumvent any access
            controls, rate limits, or technical protections of the Service or of
            any source platform;
          </li>
          <li>
            interfere with, disrupt, or attempt to gain unauthorized access to
            the Service, its infrastructure, or other users&apos; accounts.
          </li>
        </ul>
        <p>
          Additional rules for sourced content are described in our{" "}
          <a href="/content-policy" className="underline font-bold">Content Policy</a>.
        </p>
      </LegalSection>

      <LegalSection heading="5. Intellectual property">
        <p>
          The Service itself, including its software, design, branding, and the
          organization and presentation of content, is owned by Curio and
          protected by intellectual property laws. We grant you a limited,
          non-exclusive, non-transferable, revocable license to use the Service
          for your personal, non-commercial learning. All rights in third-party
          content remain with their respective owners.
        </p>
      </LegalSection>

      <LegalSection heading="6. Disclaimers">
        <p>
          The Service is provided &quot;as is&quot; and &quot;as available&quot;
          without warranties of any kind, whether express or implied, including
          implied warranties of merchantability, fitness for a particular
          purpose, and non-infringement. We do not warrant that the Service will
          be uninterrupted, error-free, or that any content is accurate or
          complete. Educational content is provided for general learning and is
          not professional advice.
        </p>
      </LegalSection>

      <LegalSection heading="7. Limitation of liability">
        <p>
          To the fullest extent permitted by law, Curio and its affiliates will
          not be liable for any indirect, incidental, special, consequential, or
          punitive damages, or for any loss of data, use, or goodwill, arising
          out of or related to your use of the Service. Our total liability for
          any claim relating to the Service will not exceed the greater of the
          amount you paid us in the twelve months before the claim or USD 100.
        </p>
      </LegalSection>

      <LegalSection heading="8. Changes & termination">
        <p>
          We may update these Terms from time to time. If we make material
          changes, we will update the &quot;Last updated&quot; date above and,
          where appropriate, provide additional notice. Your continued use of
          the Service after changes take effect constitutes acceptance. We may
          suspend or terminate your access if you violate these Terms or to
          protect the Service or other users; you may stop using the Service at
          any time.
        </p>
      </LegalSection>

      <LegalSection heading="9. Governing law">
        <p>
          These Terms are governed by the laws of the jurisdiction in which
          Curio is established, without regard to its conflict-of-laws rules.
          [Confirm the governing-law and dispute-resolution terms with counsel
          for your jurisdiction.]
        </p>
      </LegalSection>

      <LegalSection heading="10. Contact">
        <p>
          Questions about these Terms? Contact us at{" "}
          <a href="mailto:legal@curio.app" className="underline font-bold">legal@curio.app</a>.
        </p>
      </LegalSection>

      <p className="text-ink/50 text-xs pt-2">
        This document is provided for general informational purposes and is not
        legal advice.
      </p>
    </LegalLayout>
  );
}
