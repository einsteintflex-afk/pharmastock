--
-- PostgreSQL database dump
--

\restrict 17umqBgY1HAnKbUSjTbFhGTuh362VBl0DQPfPkkitcCIlvoQU6aTuDMjPBXH9mh

-- Dumped from database version 18.6
-- Dumped by pg_dump version 18.6

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.batches (
    id integer NOT NULL,
    medicine_id integer NOT NULL,
    batch_number character varying(100) NOT NULL,
    quantity integer NOT NULL,
    expiry_date date NOT NULL
);


--
-- Name: batches_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.batches_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: batches_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.batches_id_seq OWNED BY public.batches.id;


--
-- Name: medicines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.medicines (
    id integer NOT NULL,
    name character varying(150) NOT NULL,
    strength character varying(50),
    dosage_form character varying(50),
    reorder_level integer DEFAULT 20 NOT NULL
);


--
-- Name: medicines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.medicines_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: medicines_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.medicines_id_seq OWNED BY public.medicines.id;


--
-- Name: purchase_order_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.purchase_order_items (
    id integer NOT NULL,
    purchase_order_id integer NOT NULL,
    medicine_id integer NOT NULL,
    quantity_ordered integer NOT NULL,
    unit_cost numeric(12,2) NOT NULL,
    quantity_received integer DEFAULT 0 NOT NULL,
    CONSTRAINT quantity_ordered_positive CHECK ((quantity_ordered > 0)),
    CONSTRAINT quantity_received_non_negative CHECK ((quantity_received >= 0)),
    CONSTRAINT quantity_received_not_more_than_ordered CHECK ((quantity_received <= quantity_ordered)),
    CONSTRAINT unit_cost_non_negative CHECK ((unit_cost >= (0)::numeric))
);


--
-- Name: purchase_order_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.purchase_order_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: purchase_order_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.purchase_order_items_id_seq OWNED BY public.purchase_order_items.id;


--
-- Name: purchase_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.purchase_orders (
    id integer NOT NULL,
    supplier_id integer NOT NULL,
    order_number character varying(50) NOT NULL,
    order_date date DEFAULT CURRENT_DATE NOT NULL,
    status character varying(30) DEFAULT 'DRAFT'::character varying NOT NULL,
    notes text,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT purchase_order_status_check CHECK (((status)::text = ANY ((ARRAY['DRAFT'::character varying, 'ORDERED'::character varying, 'PARTIALLY_RECEIVED'::character varying, 'RECEIVED'::character varying, 'CANCELLED'::character varying])::text[])))
);


--
-- Name: purchase_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.purchase_orders_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: purchase_orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.purchase_orders_id_seq OWNED BY public.purchase_orders.id;


--
-- Name: purchase_receipts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.purchase_receipts (
    id integer NOT NULL,
    purchase_order_item_id integer NOT NULL,
    batch_id integer NOT NULL,
    quantity_received integer NOT NULL,
    received_date timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    received_by character varying(150),
    notes text,
    CONSTRAINT receipt_quantity_positive CHECK ((quantity_received > 0))
);


--
-- Name: purchase_receipts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.purchase_receipts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: purchase_receipts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.purchase_receipts_id_seq OWNED BY public.purchase_receipts.id;


--
-- Name: stock_movements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.stock_movements (
    id integer NOT NULL,
    batch_id integer NOT NULL,
    movement_type character varying(20) NOT NULL,
    quantity integer NOT NULL,
    movement_date timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    reason character varying(255)
);


--
-- Name: stock_movements_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.stock_movements_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: stock_movements_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.stock_movements_id_seq OWNED BY public.stock_movements.id;


--
-- Name: suppliers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.suppliers (
    id integer NOT NULL,
    name character varying(150) NOT NULL,
    contact_person character varying(150),
    phone character varying(50),
    email character varying(150),
    address text,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP NOT NULL
);


--
-- Name: suppliers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.suppliers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: suppliers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.suppliers_id_seq OWNED BY public.suppliers.id;


--
-- Name: batches id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batches ALTER COLUMN id SET DEFAULT nextval('public.batches_id_seq'::regclass);


--
-- Name: medicines id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.medicines ALTER COLUMN id SET DEFAULT nextval('public.medicines_id_seq'::regclass);


--
-- Name: purchase_order_items id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_order_items ALTER COLUMN id SET DEFAULT nextval('public.purchase_order_items_id_seq'::regclass);


--
-- Name: purchase_orders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders ALTER COLUMN id SET DEFAULT nextval('public.purchase_orders_id_seq'::regclass);


--
-- Name: purchase_receipts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_receipts ALTER COLUMN id SET DEFAULT nextval('public.purchase_receipts_id_seq'::regclass);


--
-- Name: stock_movements id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stock_movements ALTER COLUMN id SET DEFAULT nextval('public.stock_movements_id_seq'::regclass);


--
-- Name: suppliers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.suppliers ALTER COLUMN id SET DEFAULT nextval('public.suppliers_id_seq'::regclass);


--
-- Name: batches batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batches
    ADD CONSTRAINT batches_pkey PRIMARY KEY (id);


--
-- Name: medicines medicines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.medicines
    ADD CONSTRAINT medicines_pkey PRIMARY KEY (id);


--
-- Name: purchase_order_items purchase_order_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_order_items
    ADD CONSTRAINT purchase_order_items_pkey PRIMARY KEY (id);


--
-- Name: purchase_orders purchase_orders_order_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_order_number_key UNIQUE (order_number);


--
-- Name: purchase_orders purchase_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_pkey PRIMARY KEY (id);


--
-- Name: purchase_receipts purchase_receipts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_receipts
    ADD CONSTRAINT purchase_receipts_pkey PRIMARY KEY (id);


--
-- Name: stock_movements stock_movements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stock_movements
    ADD CONSTRAINT stock_movements_pkey PRIMARY KEY (id);


--
-- Name: suppliers suppliers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.suppliers
    ADD CONSTRAINT suppliers_pkey PRIMARY KEY (id);


--
-- Name: idx_purchase_order_items_medicine; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_purchase_order_items_medicine ON public.purchase_order_items USING btree (medicine_id);


--
-- Name: idx_purchase_order_items_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_purchase_order_items_order ON public.purchase_order_items USING btree (purchase_order_id);


--
-- Name: idx_purchase_orders_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_purchase_orders_status ON public.purchase_orders USING btree (status);


--
-- Name: idx_purchase_orders_supplier; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_purchase_orders_supplier ON public.purchase_orders USING btree (supplier_id);


--
-- Name: batches batches_medicine_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.batches
    ADD CONSTRAINT batches_medicine_id_fkey FOREIGN KEY (medicine_id) REFERENCES public.medicines(id);


--
-- Name: purchase_order_items purchase_order_items_medicine_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_order_items
    ADD CONSTRAINT purchase_order_items_medicine_id_fkey FOREIGN KEY (medicine_id) REFERENCES public.medicines(id);


--
-- Name: purchase_order_items purchase_order_items_purchase_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_order_items
    ADD CONSTRAINT purchase_order_items_purchase_order_id_fkey FOREIGN KEY (purchase_order_id) REFERENCES public.purchase_orders(id) ON DELETE CASCADE;


--
-- Name: purchase_orders purchase_orders_supplier_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_orders
    ADD CONSTRAINT purchase_orders_supplier_id_fkey FOREIGN KEY (supplier_id) REFERENCES public.suppliers(id);


--
-- Name: purchase_receipts purchase_receipts_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_receipts
    ADD CONSTRAINT purchase_receipts_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.batches(id);


--
-- Name: purchase_receipts purchase_receipts_purchase_order_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.purchase_receipts
    ADD CONSTRAINT purchase_receipts_purchase_order_item_id_fkey FOREIGN KEY (purchase_order_item_id) REFERENCES public.purchase_order_items(id) ON DELETE CASCADE;


--
-- Name: stock_movements stock_movements_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.stock_movements
    ADD CONSTRAINT stock_movements_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.batches(id);


--
-- PostgreSQL database dump complete
--

\unrestrict 17umqBgY1HAnKbUSjTbFhGTuh362VBl0DQPfPkkitcCIlvoQU6aTuDMjPBXH9mh

