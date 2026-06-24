import type { Metadata } from "next";
import LegalLayout, { LegalSection } from "@/components/LegalLayout";

export const metadata: Metadata = {
  title: "Privacy Policy · Curio",
  description: "How Curio collects, uses, and protects your data.",
};

// Generated starting draft, review with counsel before relying on it, and
// confirm every statement matches your real data practices. Update contact
// addresses and add GDPR/CCPA-specific disclosures as your user base requires.
export default function PrivacyPage() {
  return (
    <LegalLayout title="Privacy Policy" lastUpdated="June 20, 2026">
      <p>
        This Privacy Policy explains what information Curio (&quot;Curio,&quot;
        &quot;we,&quot; &quot;us&quot;) collects when you use our website and
        services (the &quot;Service&quot;), how we use it, who we share it with,
        and the choices you have. By using the Service, you agree to the
        practices described here.
      </p>

      <LegalSection heading="1. Information we collect">
        <p>We collect the following categories of information:</p>
        <ul className="list-disc pl-5 space-y-1">
          <li>
            <span className="font-bold">Account information</span>, such as your
            email address, provided through our authentication provider when you
            sign up or sign in.
          </li>
          <li>
            <span className="font-bold">Learning activity</span>, the topics you
            search, the learning paths generated for you, the clips served to
            you, watch and skip events, replays, and quiz answers.
          </li>
          <li>
            <span className="font-bold">Derived signals</span>, interest and
            taste profiles we compute from your activity to personalize and rank
            your feed.
          </li>
          <li>
            <span className="font-bold">Device & usage data</span>, basic
            technical information such as browser type, device, and interactions,
            collected to operate and improve the Service.
          </li>
        </ul>
      </LegalSection>

      <LegalSection heading="2. How we use information">
        <p>We use the information we collect to:</p>
        <ul className="list-disc pl-5 space-y-1">
          <li>build and deliver your learning paths and personalized feed;</li>
          <li>rank and recommend content based on your interests and activity;</li>
          <li>measure engagement and improve the quality of the Service;</li>
          <li>maintain security, prevent abuse, and comply with legal obligations.</li>
        </ul>
        <p>
          Where required by law, we rely on a lawful basis such as your consent,
          the performance of our agreement with you, or our legitimate interests
          in operating the Service.
        </p>
      </LegalSection>

      <LegalSection heading="3. Third-party services & embedded players">
        <p>
          We rely on trusted third parties to run the Service, including an
          authentication and database provider, hosting and analytics providers.
          When a clip plays, it loads through an embedded player hosted by the
          source video platform (such as YouTube). Those embedded players may set
          their own cookies and receive playback and device data directly,
          governed by the source platform&apos;s own privacy policy. We do not
          control and are not responsible for those third-party practices.
        </p>
      </LegalSection>

      <LegalSection heading="4. Cookies & local storage">
        <p>
          We use cookies and browser local storage to keep you signed in, to
          remember guest progress and onboarding state, and to understand how the
          Service is used. You can control cookies through your browser settings;
          disabling them may affect functionality such as staying signed in.
        </p>
      </LegalSection>

      <LegalSection heading="5. How we share information">
        <p>
          We do not sell your personal information. We share it only with the
          service providers described above to operate the Service, when required
          by law or legal process, or in connection with a business transfer such
          as a merger or acquisition (with notice where required).
        </p>
      </LegalSection>

      <LegalSection heading="6. Data retention">
        <p>
          We retain your information for as long as your account is active or as
          needed to provide the Service, and thereafter only as necessary to
          comply with our legal obligations, resolve disputes, and enforce our
          agreements. You may request deletion of your account and associated
          data as described below.
        </p>
      </LegalSection>

      <LegalSection heading="7. Your rights & choices">
        <p>
          Depending on where you live, you may have the right to access, correct,
          delete, or port your personal information, to object to or restrict
          certain processing, and to withdraw consent. To exercise these rights,
          contact us at the address below. We will respond within the timeframe
          required by applicable law. EU/UK users have rights under the GDPR;
          California residents have rights under the CCPA/CPRA.
        </p>
      </LegalSection>

      <LegalSection heading="8. Children's privacy">
        <p>
          The Service is intended for general audiences and is not directed to
          children under 13. We do not knowingly collect personal information
          from children under 13. If you believe a child has provided us personal
          information, contact us and we will take appropriate steps to delete it.
          [If you intend to serve children, additional COPPA obligations apply,
          confirm with counsel.]
        </p>
      </LegalSection>

      <LegalSection heading="9. Changes to this policy">
        <p>
          We may update this Privacy Policy from time to time. We will revise the
          &quot;Last updated&quot; date above and, for material changes, provide
          additional notice where appropriate.
        </p>
      </LegalSection>

      <LegalSection heading="10. Contact">
        <p>
          Questions or privacy requests? Contact us at{" "}
          <a href="mailto:privacy@curio.app" className="underline font-bold">privacy@curio.app</a>.
        </p>
      </LegalSection>

      <p className="text-ink/50 text-xs pt-2">
        This document is provided for general informational purposes and is not
        legal advice.
      </p>
    </LegalLayout>
  );
}
