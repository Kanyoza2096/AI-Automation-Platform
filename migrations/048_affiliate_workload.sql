-- Phase 57: governed affiliate workload.
-- Affiliate is a workload on the existing Action Kernel, not a second execution plane.

CREATE TABLE IF NOT EXISTS public.affiliate_products (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id TEXT NOT NULL,
  name TEXT NOT NULL,
  provider TEXT NOT NULL,
  program TEXT NOT NULL,
  destination_domains JSONB NOT NULL DEFAULT '[]'::jsonb,
  commission_rate NUMERIC(8,6) NOT NULL DEFAULT 0 CHECK (commission_rate >= 0 AND commission_rate <= 1),
  currency TEXT NOT NULL DEFAULT 'USD',
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','archived')),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_affiliate_products_workspace ON public.affiliate_products(workspace_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.affiliate_links (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id TEXT NOT NULL,
  product_id UUID NOT NULL REFERENCES public.affiliate_products(id) ON DELETE RESTRICT,
  slug TEXT NOT NULL UNIQUE,
  destination_url TEXT NOT NULL,
  campaign TEXT NOT NULL DEFAULT '',
  content_id TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','archived')),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_affiliate_links_workspace ON public.affiliate_links(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_affiliate_links_product ON public.affiliate_links(product_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.affiliate_clicks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id TEXT NOT NULL,
  link_id UUID NOT NULL REFERENCES public.affiliate_links(id) ON DELETE RESTRICT,
  event_id TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_affiliate_click_event ON public.affiliate_clicks(event_id);
CREATE INDEX IF NOT EXISTS idx_affiliate_clicks_link_time ON public.affiliate_clicks(link_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS public.affiliate_conversions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id TEXT NOT NULL,
  product_id UUID NOT NULL REFERENCES public.affiliate_products(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL,
  external_order_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  amount NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK (amount >= 0),
  commission NUMERIC(18,4) NOT NULL DEFAULT 0 CHECK (commission >= 0),
  currency TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','approved','reversed','paid')),
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(workspace_id, provider, external_order_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_affiliate_conversion_event ON public.affiliate_conversions(event_id);
CREATE INDEX IF NOT EXISTS idx_affiliate_conversions_workspace_time ON public.affiliate_conversions(workspace_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS public.affiliate_conversion_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id TEXT NOT NULL,
  product_id UUID NOT NULL REFERENCES public.affiliate_products(id) ON DELETE RESTRICT,
  provider TEXT NOT NULL,
  external_order_id TEXT NOT NULL,
  event_id TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_affiliate_conversion_events_workspace ON public.affiliate_conversion_events(workspace_id, received_at DESC);

CREATE TABLE IF NOT EXISTS public.affiliate_revenue_daily (
  workspace_id TEXT NOT NULL,
  product_id UUID NOT NULL REFERENCES public.affiliate_products(id) ON DELETE RESTRICT,
  day DATE NOT NULL,
  clicks BIGINT NOT NULL DEFAULT 0,
  conversions BIGINT NOT NULL DEFAULT 0,
  revenue NUMERIC(18,4) NOT NULL DEFAULT 0,
  commission NUMERIC(18,4) NOT NULL DEFAULT 0,
  currency TEXT NOT NULL DEFAULT 'USD',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id, product_id, day)
);

ALTER TABLE public.affiliate_products ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.affiliate_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.affiliate_clicks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.affiliate_conversions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.affiliate_conversion_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.affiliate_revenue_daily ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['affiliate_products','affiliate_links','affiliate_clicks','affiliate_conversions','affiliate_conversion_events','affiliate_revenue_daily'] LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I_workspace_select ON public.%I', t, t);
    EXECUTE format('CREATE POLICY %I_workspace_select ON public.%I FOR SELECT USING (workspace_id IN (SELECT workspace_id FROM public.workspace_members WHERE user_id = auth.uid()::text))', t, t);
    EXECUTE format('DROP POLICY IF EXISTS %I_workspace_write ON public.%I', t, t);
    EXECUTE format('CREATE POLICY %I_workspace_write ON public.%I FOR ALL USING (workspace_id IN (SELECT workspace_id FROM public.workspace_members WHERE user_id = auth.uid()::text)) WITH CHECK (workspace_id IN (SELECT workspace_id FROM public.workspace_members WHERE user_id = auth.uid()::text))', t, t);
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION public.record_affiliate_click(
  p_link_id UUID, p_workspace_id TEXT DEFAULT NULL, p_event_id TEXT DEFAULT NULL, p_metadata JSONB DEFAULT '{}'::jsonb
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE v_link affiliate_links%ROWTYPE; v_event TEXT := COALESCE(NULLIF(p_event_id,''), gen_random_uuid()::text); v_id UUID; v_day DATE := current_date;
BEGIN
  SELECT * INTO v_link FROM public.affiliate_links WHERE id = p_link_id AND status = 'active' FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'affiliate link not found'; END IF;
  IF p_workspace_id IS NOT NULL AND p_workspace_id <> v_link.workspace_id THEN RAISE EXCEPTION 'workspace mismatch'; END IF;
  INSERT INTO public.affiliate_clicks(workspace_id, link_id, event_id, metadata)
  VALUES(v_link.workspace_id, v_link.id, v_event, COALESCE(p_metadata,'{}'::jsonb))
  ON CONFLICT (event_id) DO NOTHING
  RETURNING id INTO v_id;
  IF v_id IS NOT NULL THEN
    INSERT INTO public.affiliate_revenue_daily(workspace_id, product_id, day, clicks, currency)
    SELECT v_link.workspace_id, v_link.product_id, v_day, 1, p.currency
    FROM public.affiliate_products p WHERE p.id = v_link.product_id
    ON CONFLICT (workspace_id, product_id, day) DO UPDATE SET clicks = public.affiliate_revenue_daily.clicks + 1, updated_at = now();
  END IF;
  RETURN jsonb_build_object('ok', true, 'recorded', v_id IS NOT NULL, 'event_id', v_event);
END;
$$;
REVOKE ALL ON FUNCTION public.record_affiliate_click(UUID,TEXT,TEXT,JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.record_affiliate_click(UUID,TEXT,TEXT,JSONB) TO service_role;

CREATE OR REPLACE FUNCTION public.upsert_affiliate_conversion(p_payload JSONB)
RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE p JSONB := COALESCE(p_payload,'{}'::jsonb); v_product affiliate_products%ROWTYPE; v_existing affiliate_conversions%ROWTYPE; v_day DATE; v_count BIGINT; v_revenue NUMERIC; v_commission NUMERIC;
BEGIN
  SELECT * INTO v_product FROM public.affiliate_products WHERE id = (p->>'product_id')::uuid;
  IF NOT FOUND THEN RAISE EXCEPTION 'affiliate product not found'; END IF;
  IF lower(v_product.provider) <> lower(p->>'provider') THEN RAISE EXCEPTION 'provider mismatch'; END IF;
  INSERT INTO public.affiliate_conversion_events(workspace_id, product_id, provider, external_order_id, event_id, status, payload)
  VALUES(v_product.workspace_id, v_product.id, lower(p->>'provider'), p->>'external_order_id', p->>'event_id', p->>'status', p)
  ON CONFLICT (event_id) DO NOTHING;
  IF NOT FOUND THEN
    RETURN jsonb_build_object('ok', true, 'duplicate', true, 'event_id', p->>'event_id');
  END IF;
  INSERT INTO public.affiliate_conversions(workspace_id, product_id, provider, external_order_id, event_id, amount, commission, currency, status, occurred_at, metadata)
  VALUES(v_product.workspace_id, v_product.id, lower(p->>'provider'), p->>'external_order_id', p->>'event_id', (p->>'amount')::numeric, (p->>'commission')::numeric, upper(p->>'currency'), p->>'status', COALESCE((p->>'occurred_at')::timestamptz, now()), COALESCE(p->'metadata','{}'::jsonb))
  ON CONFLICT (workspace_id, provider, external_order_id) DO UPDATE SET
    event_id = EXCLUDED.event_id, amount = EXCLUDED.amount, commission = EXCLUDED.commission,
    currency = EXCLUDED.currency, status = EXCLUDED.status, occurred_at = EXCLUDED.occurred_at,
    metadata = EXCLUDED.metadata, updated_at = now();
  SELECT occurred_at::date INTO v_day FROM public.affiliate_conversions WHERE workspace_id=v_product.workspace_id AND provider=lower(p->>'provider') AND external_order_id=p->>'external_order_id';
  SELECT count(*), COALESCE(sum(CASE WHEN status IN ('approved','paid') THEN amount ELSE 0 END),0), COALESCE(sum(CASE WHEN status IN ('approved','paid') THEN commission ELSE 0 END),0)
    INTO v_count, v_revenue, v_commission
    FROM public.affiliate_conversions WHERE workspace_id=v_product.workspace_id AND product_id=v_product.id AND occurred_at::date=v_day;
  INSERT INTO public.affiliate_revenue_daily(workspace_id, product_id, day, conversions, revenue, commission, currency)
  VALUES(v_product.workspace_id, v_product.id, v_day, v_count, v_revenue, v_commission, upper(p->>'currency'))
  ON CONFLICT (workspace_id, product_id, day) DO UPDATE SET conversions=EXCLUDED.conversions, revenue=EXCLUDED.revenue, commission=EXCLUDED.commission, updated_at=now();
  RETURN jsonb_build_object('ok', true, 'duplicate', false, 'event_id', p->>'event_id', 'workspace_id', v_product.workspace_id);
END;
$$;
REVOKE ALL ON FUNCTION public.upsert_affiliate_conversion(JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.upsert_affiliate_conversion(JSONB) TO service_role;
