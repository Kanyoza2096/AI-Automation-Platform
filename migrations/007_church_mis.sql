-- =========================================================================
-- Kanyoza Systems — Unified Industry MIS / CRM Schema
-- Migration 007: Church, School, Hospital, and CRM tables
-- Run in Supabase Dashboard → SQL Editor → New query
-- =========================================================================

-- =========================================================================
-- 1. CHURCH MIS TABLES
-- =========================================================================

CREATE TABLE IF NOT EXISTS public.church_members (
    id              TEXT            PRIMARY KEY,
    workspace_id    TEXT            NOT NULL,
    full_name       TEXT            NOT NULL,
    phone           TEXT            NOT NULL DEFAULT '',
    email           TEXT,
    address         TEXT,
    department      TEXT            NOT NULL DEFAULT 'congregation',
    role            TEXT            NOT NULL DEFAULT 'member',
    baptism_date    TIMESTAMPTZ,
    is_active       BOOLEAN         NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_church_members_workspace
    ON public.church_members (workspace_id);

CREATE TABLE IF NOT EXISTS public.church_attendance (
    id              TEXT            PRIMARY KEY,
    workspace_id    TEXT            NOT NULL,
    member_id       TEXT            REFERENCES public.church_members(id) ON DELETE SET NULL,
    member_name     TEXT            NOT NULL DEFAULT '',
    service_date    TEXT            NOT NULL,
    service_type    TEXT            NOT NULL DEFAULT 'sunday_service',
    present         BOOLEAN         NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_church_attendance_workspace
    ON public.church_attendance (workspace_id, service_date DESC);

CREATE TABLE IF NOT EXISTS public.church_donations (
    id              TEXT            PRIMARY KEY,
    workspace_id    TEXT            NOT NULL,
    donor_id        TEXT,
    donor_name      TEXT            NOT NULL DEFAULT '',
    amount          NUMERIC(12, 2)  NOT NULL CHECK (amount >= 0),
    currency        TEXT            NOT NULL DEFAULT 'MWK',
    purpose         TEXT            NOT NULL DEFAULT 'tithe',
    payment_method  TEXT            NOT NULL DEFAULT 'cash',
    donation_date   TEXT            NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_church_donations_workspace
    ON public.church_donations (workspace_id, donation_date DESC);

CREATE TABLE IF NOT EXISTS public.church_events (
    id              TEXT            PRIMARY KEY,
    workspace_id    TEXT            NOT NULL,
    title           TEXT            NOT NULL,
    description     TEXT,
    event_date      TEXT            NOT NULL,
    start_time      TEXT,
    location        TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_church_events_workspace
    ON public.church_events (workspace_id, event_date DESC);


-- =========================================================================
-- 2. SCHOOL MIS TABLES
-- =========================================================================

CREATE TABLE IF NOT EXISTS public.school_students (
    id              TEXT            PRIMARY KEY,
    workspace_id    TEXT            NOT NULL,
    full_name       TEXT            NOT NULL,
    phone           TEXT            NOT NULL DEFAULT '',
    email           TEXT,
    address         TEXT,
    department      TEXT            NOT NULL DEFAULT 'congregation',
    role            TEXT            NOT NULL DEFAULT 'student',
    baptism_date    TIMESTAMPTZ, -- Left as baptism_date to match Pydantic model
    is_active       BOOLEAN         NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_school_students_workspace
    ON public.school_students (workspace_id);

CREATE TABLE IF NOT EXISTS public.school_attendance (
    id              TEXT            PRIMARY KEY,
    workspace_id    TEXT            NOT NULL,
    student_id      TEXT            REFERENCES public.school_students(id) ON DELETE SET NULL,
    student_name    TEXT            NOT NULL DEFAULT '',
    service_date    TEXT            NOT NULL,
    service_type    TEXT            NOT NULL DEFAULT 'sunday_service',
    present         BOOLEAN         NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_school_attendance_workspace
    ON public.school_attendance (workspace_id, service_date DESC);

CREATE TABLE IF NOT EXISTS public.school_fees (
    id              TEXT            PRIMARY KEY,
    workspace_id    TEXT            NOT NULL,
    donor_id        TEXT,
    donor_name      TEXT            NOT NULL DEFAULT '',
    amount          NUMERIC(12, 2)  NOT NULL CHECK (amount >= 0),
    currency        TEXT            NOT NULL DEFAULT 'MWK',
    purpose         TEXT            NOT NULL DEFAULT 'tithe',
    payment_method  TEXT            NOT NULL DEFAULT 'cash',
    donation_date   TEXT            NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_school_fees_workspace
    ON public.school_fees (workspace_id, donation_date DESC);

CREATE TABLE IF NOT EXISTS public.school_mis_events (
    id              TEXT            PRIMARY KEY,
    workspace_id    TEXT            NOT NULL,
    title           TEXT            NOT NULL,
    description     TEXT,
    event_date      TEXT            NOT NULL,
    start_time      TEXT,
    location        TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_school_events_workspace
    ON public.school_mis_events (workspace_id, event_date DESC);


-- =========================================================================
-- 3. HOSPITAL MIS TABLES
-- =========================================================================

CREATE TABLE IF NOT EXISTS public.hospital_patients (
    id              TEXT            PRIMARY KEY,
    workspace_id    TEXT            NOT NULL,
    full_name       TEXT            NOT NULL,
    phone           TEXT            NOT NULL DEFAULT '',
    email           TEXT,
    address         TEXT,
    department      TEXT            NOT NULL DEFAULT 'congregation',
    role            TEXT            NOT NULL DEFAULT 'patient',
    baptism_date    TIMESTAMPTZ, -- Left to match patient Pydantic model
    is_active       BOOLEAN         NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_hospital_patients_workspace
    ON public.hospital_patients (workspace_id);

CREATE TABLE IF NOT EXISTS public.hospital_vitals (
    id              TEXT            PRIMARY KEY,
    workspace_id    TEXT            NOT NULL,
    patient_id      TEXT            REFERENCES public.hospital_patients(id) ON DELETE SET NULL,
    patient_name    TEXT            NOT NULL DEFAULT '',
    service_date    TEXT            NOT NULL,
    service_type    TEXT            NOT NULL DEFAULT 'sunday_service',
    present         BOOLEAN         NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_hospital_vitals_workspace
    ON public.hospital_vitals (workspace_id, service_date DESC);

CREATE TABLE IF NOT EXISTS public.hospital_billing (
    id              TEXT            PRIMARY KEY,
    workspace_id    TEXT            NOT NULL,
    donor_id        TEXT,
    donor_name      TEXT            NOT NULL DEFAULT '',
    amount          NUMERIC(12, 2)  NOT NULL CHECK (amount >= 0),
    currency        TEXT            NOT NULL DEFAULT 'MWK',
    purpose         TEXT            NOT NULL DEFAULT 'tithe',
    payment_method  TEXT            NOT NULL DEFAULT 'cash',
    donation_date   TEXT            NOT NULL,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_hospital_billing_workspace
    ON public.hospital_billing (workspace_id, donation_date DESC);

CREATE TABLE IF NOT EXISTS public.hospital_events (
    id              TEXT            PRIMARY KEY,
    workspace_id    TEXT            NOT NULL,
    title           TEXT            NOT NULL,
    description     TEXT,
    event_date      TEXT            NOT NULL,
    start_time      TEXT,
    location        TEXT,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_hospital_events_workspace
    ON public.hospital_events (workspace_id, event_date DESC);


-- =========================================================================
-- 4. CRM TABLES
-- =========================================================================

CREATE TABLE IF NOT EXISTS public.crm_contacts (
    id              TEXT            PRIMARY KEY,
    workspace_id    TEXT            NOT NULL,
    contact_name    TEXT            NOT NULL,
    phone           TEXT            NOT NULL DEFAULT '',
    email           TEXT,
    company         TEXT,
    status          TEXT            NOT NULL DEFAULT 'lead',
    source          TEXT,
    notes           TEXT,
    is_active       BOOLEAN         NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_crm_contacts_workspace
    ON public.crm_contacts (workspace_id);

CREATE TABLE IF NOT EXISTS public.crm_deals (
    id              TEXT            PRIMARY KEY,
    workspace_id    TEXT            NOT NULL,
    contact_id      TEXT            NOT NULL,
    contact_name    TEXT            NOT NULL DEFAULT '',
    amount          NUMERIC(12, 2)  NOT NULL DEFAULT 0,
    stage           TEXT            NOT NULL DEFAULT 'lead',
    deal_date       TEXT            NOT NULL,
    expected_close_date TEXT,
    probability     INTEGER         NOT NULL DEFAULT 20,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_crm_deals_workspace
    ON public.crm_deals (workspace_id, deal_date DESC);

CREATE TABLE IF NOT EXISTS public.crm_followups (
    id              TEXT            PRIMARY KEY,
    workspace_id    TEXT            NOT NULL,
    contact_id      TEXT            NOT NULL DEFAULT '',
    contact_name    TEXT            NOT NULL DEFAULT '',
    followup_date   TEXT            NOT NULL,
    notes           TEXT            NOT NULL DEFAULT '',
    status          TEXT            NOT NULL DEFAULT 'scheduled',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_crm_followups_workspace
    ON public.crm_followups (workspace_id, followup_date DESC);

CREATE TABLE IF NOT EXISTS public.crm_meetings (
    id              TEXT            PRIMARY KEY,
    workspace_id    TEXT            NOT NULL,
    title           TEXT            NOT NULL,
    description     TEXT,
    meeting_date    TEXT            NOT NULL,
    start_time      TEXT,
    location        TEXT            NOT NULL DEFAULT 'Virtual',
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_crm_meetings_workspace
    ON public.crm_meetings (workspace_id, meeting_date DESC);


-- =========================================================================
-- Disable RLS to allow direct service_role bypass matching the platform design
-- =========================================================================

ALTER TABLE public.church_members       DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.church_attendance    DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.church_donations     DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.church_events        DISABLE ROW LEVEL SECURITY;

ALTER TABLE public.school_students      DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.school_attendance    DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.school_fees          DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.school_mis_events    DISABLE ROW LEVEL SECURITY;

ALTER TABLE public.hospital_patients    DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.hospital_vitals      DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.hospital_billing     DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.hospital_events      DISABLE ROW LEVEL SECURITY;

ALTER TABLE public.crm_contacts         DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.crm_deals            DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.crm_followups        DISABLE ROW LEVEL SECURITY;
ALTER TABLE public.crm_meetings         DISABLE ROW LEVEL SECURITY;
