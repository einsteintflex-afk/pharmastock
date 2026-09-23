--
-- PostgreSQL database dump
--

\restrict vPyfDqkGu498o2zs8itcb9augPKK7OJNSs6dMRmnsXV0KHssLoyonXbg9FoaF4R

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

--
-- Data for Name: medicines; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.medicines VALUES (1, 'Paracetamol', '500 mg', 'Tablet', 20);
INSERT INTO public.medicines VALUES (2, 'Amoxicillin', '500 mg', 'Capsule', 20);
INSERT INTO public.medicines VALUES (4, 'Ibuprofen', '400 mg', 'Tablet', 60);


--
-- Data for Name: batches; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.batches VALUES (1, 1, 'PARA001', 100, '2027-06-30');
INSERT INTO public.batches VALUES (2, 1, 'PARA002', 50, '2026-10-05');
INSERT INTO public.batches VALUES (3, 1, 'PARA003', 30, '2026-11-14');
INSERT INTO public.batches VALUES (4, 1, 'PARA004', 10, '2026-09-05');
INSERT INTO public.batches VALUES (5, 2, 'AMOX001', 180, '2027-08-30');
INSERT INTO public.batches VALUES (6, 2, 'AMOX002', 100, '2028-09-30');


--
-- Data for Name: suppliers; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.suppliers VALUES (1, 'MedSupply Ghana Ltd', 'Kwame Mensah', '0244000000', 'info@medsupply.example', 'Kumasi, Ghana', true, '2026-09-16 11:42:44.542326');


--
-- Data for Name: purchase_orders; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.purchase_orders VALUES (1, 1, 'PO-2026-001', '2026-09-16', 'RECEIVED', 'Initial test purchase order', '2026-09-16 11:44:58.935378');


--
-- Data for Name: purchase_order_items; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.purchase_order_items VALUES (1, 1, 2, 100, 5.50, 100);


--
-- Data for Name: purchase_receipts; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.purchase_receipts VALUES (1, 1, 6, 100, '2026-09-16 11:49:24.837241', 'Pharmacy Store', 'Initial test receipt');


--
-- Data for Name: stock_movements; Type: TABLE DATA; Schema: public; Owner: -
--

INSERT INTO public.stock_movements VALUES (1, 5, 'DISPENSED', 20, '2026-09-15 09:06:05.184378', 'Test stock dispensing');
INSERT INTO public.stock_movements VALUES (2, 6, 'RECEIVED', 100, '2026-09-16 11:49:24.837241', 'Purchase order received');


--
-- Name: batches_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.batches_id_seq', 6, true);


--
-- Name: medicines_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.medicines_id_seq', 4, true);


--
-- Name: purchase_order_items_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.purchase_order_items_id_seq', 1, true);


--
-- Name: purchase_orders_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.purchase_orders_id_seq', 1, true);


--
-- Name: purchase_receipts_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.purchase_receipts_id_seq', 1, true);


--
-- Name: stock_movements_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.stock_movements_id_seq', 2, true);


--
-- Name: suppliers_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.suppliers_id_seq', 1, true);


--
-- PostgreSQL database dump complete
--

\unrestrict vPyfDqkGu498o2zs8itcb9augPKK7OJNSs6dMRmnsXV0KHssLoyonXbg9FoaF4R

