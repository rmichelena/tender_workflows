**ITB-ICAO-00068**

**New VSAT-Radar Network**

**SET 3**

1) Regarding the evaluation criterion "Bidder provided local representation information", can you please confirm that such local representation can be fulfilled by having a suitable sub-contractor in place who is able to comply with the requirements set forth in the Technical Specifications?

If so, we would also appreciate additional guidance on any minimum qualifications, certifications, or service capabilities expected from such a local representative. If not, can you please provide guidance on what the expectations are regarding such local representation to comply with the tender requirement?

**RESPONSE:**

That is acceptable. Local representation can be fulfilled by having a suitable sub-contractor in place who is able to comply with the requirements set forth in the technical specifications. The local representative shall have the necessary qualifications and experience in order to provide the services requested in paragraphs 7.9.2 and 7.9.3 of the specifications.

2) "Consulta Técnica sobre Cumplimiento con la Norma EUROCAE ED-137En el marco de las Especificaciones Técnicas para la Compra de la Nueva Red VSAT-Radar (Proyecto PER24819, Número de PR ICAO-FOS-1000153), hemos revisado detalladamente los requisitos para el diseño, suministro e integración de la red satelital, con énfasis en el transporte de extremo a extremo de servicios aeronáuticos como los canales de voz VHF-ER (alcance ampliado), datos de radar y ADS-B, y canales de coordinación técnica, tal como se describe en el Alcance (página 4-5), los Objetivos Específicos (página 5) y el Anexo III - Características de los Enlaces por Sitio (páginas 143-200 aprox.), donde se detallan las interfaces analógicas (e.g., FXS para voz VHF-ER en frecuencias como 128.8 MHz, 124.75 MHz, etc.) que deben ser multiplexadas e integradas en los equipos de acceso (multiplexores, switches Ethernet, enrutadores) para su transporte vía un solo salto satelital en banda C. En particular, notamos que las especificaciones hacen referencia explícita a la norma EUROCAE ED-137/1C (o equivalentes) en los siguientes puntos clave, alineados con los requisitos de la Regulación Aeronáutica del Perú (RAP) 310 - Telecomunicaciones Aeronáuticas(mencionada en los Objetivos Específicos, página 5) y el cumplimiento de estándares internacionales para interoperabilidad y calidad en comunicaciones ATS (Air Traffic Services):

* Punto 5.2.7 (b): Se exige que los equipos de acceso para servicios de voz (incluyendo VHF-ER) soporten conversión analógico-digital con compresión de voz conforme a ED-137 para minimizar latencia y jitter en entornos satelitales, asegurando el transporte sin degradación en la red multiservicio. El Punto 5.2.7
* Punto 5.5.11 (j): Se requiere integración de protocolos de red que incluyan ED-137 para la conmutación automática entre medios (Nueva VSAT-Radar como principal, VSAT-Radar Actual como secundario opcional, y REDAP como terciario), garantizando resiliencia y continuidad en operaciones aéreas críticas.
* Punto 5.5.17: En el contexto de los sistemas de gestión (r-NMS/l-NMS), se menciona el monitoreo y configuración de interfaces que cumplan con ED-137 para diagnóstico en tiempo real, facilitando el cumplimiento de la disponibilidad mínima del 99.998% para nodos VSAT (configuración 1+1 HS, página 6-7).

Estos requisitos subrayan el compromiso de la solución con la eficiencia, seguridad y regularidad de las operaciones aéreas (Introducción y Finalidad, página 1), promoviendo tecnologías probadas que reduzcan interferencias (e.g., filtros RF contra WiMAX/5G, página 7) y aseguren un MTBF elevado (Requisitos Técnicos Genéricos, página 20-25). Nuestra comprensión y propuesta alineada: Entendemos que, para optimizar el rendimiento en la Nueva Red VSAT-Radar (compuesta por 8 nodos, incluyendo Master en Lima e Iquitos como backup, y remotos en Rayado, Acopia, Collpayoc, Talara, Pucallpa y Toccto), las interfaces analógicas (e.g., FXS para canales VHF-ER en Tablas III-1-2, III-2-2, etc., del Anexo III) deben ser convertidas a formato digital IP antes de su multiplexación y transporte satelital. Esto no solo alinea con las menciones a ED-137, sino que también habilita una integración fluida con la REDAP (WAN MPLS, capa 2.5 OSI, Anexo I, página 140) y la VSAT-Radar Actual (en renovación vía Anexo IV), minimizando jitter inherente en circuitos "Real Time" (Anexo I, página 141) y cumpliendo con los objetivos de mejora en seguridad operativa (página 5). Dado que la especificación no lo declara de manera directa para todos los servicios analógicos, pero sí lo implica en los puntos citados para garantizar interoperabilidad y escalabilidad (e.g., capacidad de expansión del 30% en ancho de banda, página 25), nos gustaría confirmar nuestra interpretación para asegurar una oferta fully compliant. Pregunta de aclaración: ¿Confirma CORPAC que, para aquellos servicios e interfaces analógicas (como los canales VHF-ER y de coordinación técnica detallados en el Anexo III) que requieran conversión a formato digital para su transporte en la Nueva Red VSAT-Radar, es obligatorio el cumplimiento con la norma EUROCAE ED-137/1C (o equivalente superior), como se infiere de los puntos 5.2.7 (b), 5.5.11 (j) y 5.5.11(t)? Esto nos permitiría proponer una solución que maximice la compatibilidad con estándares OACI y RAP 310, contribuyendo directamente a la resiliencia de la red por los próximos 10 años (Objetivo General, página 5).

\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

"Technical Query Regarding Compliance with EUROCAE ED-137 Within the framework of the Technical Specifications for the Procurement of the New VSAT-Radar Network (Project PER24819, PR Number ICAO-FOS-1000153), we have carefully reviewed the requirements for the design, supply, and integration of the satellite network, with particular emphasis on the end-to-end transport of aeronautical services such as extended-range VHF-ER voice channels, radar data, ADS-B, and technical coordination channels, as described in the Scope (pages 4–5), Specific Objectives (page 5), and Annex III – Site-Specific Link Characteristics (approx. pages 143–200), where analog interfaces (e.g., FXS for VHF-ER voice at frequencies such as 128.8 MHz, 124.75 MHz, etc.) must be multiplexed and integrated into the access equipment (multiplexers, Ethernet switches, routers) for transport via a single satellite hop in standard C-band. In particular, we note that the specifications explicitly reference the EUROCAE ED-137/1C standard (or equivalents) in the following key sections, aligned with the requirements of the Peruvian Aeronautical Regulation (RAP) 310 – Aeronautical Telecommunications (mentioned in Specific Objectives, page 5) and international standards for interoperability and quality in ATS (Air Traffic Services) communications:

* Section 5.2.7 (b): It requires that access equipment for voice services (including VHF-ER) support analog-to-digital conversion with voice compression compliant with ED-137 to minimize latency and jitter in satellite environments, ensuring degradation-free transport across the multiservice network.
* Section 5.5.11 (j): It mandates the integration of network protocols that include ED-137 for automatic switching between media (New VSAT-Radar as primary, optionally VSAT-Radar Current as secondary, and REDAP as tertiary), guaranteeing resilience and continuity in critical air operations.
* Section 5.5.17: In the context of management systems (r-NMS / l-NMS), it mentions monitoring and configuration of interfaces compliant with ED-137 for real-time diagnostics, facilitating compliance with the minimum availability of 99.998% for VSAT nodes (1+1 HS configuration, pages 6–7).

These requirements underscore the solution's commitment to efficiency, safety, and regularity of air operations (Introduction and Public Purpose, page 1), promoting proven technologies that reduce interference (e.g., RF bandpass filters against WiMAX/5G, page 7) and ensure high MTBF (General Technical Requirements, pages 20–25). Our understanding and aligned proposal: We interpret that, in order to optimize performance in the New VSAT-Radar Network (comprising 8 nodes, including Master in Lima and backup Master in Iquitos, and remotes at Rayado, Acopia, Collpayoc, Talara, Pucallpa, and Toccto), analog interfaces (e.g., FXS for VHF-ER channels listed in Tables III-1-2, III-2-2, etc. of Annex III) must be converted to digital IP format prior to multiplexing and satellite transport. This not only aligns with the explicit references to ED-137, but also enables seamless integration with REDAP (MPLS WAN, OSI layer 2.5, Annex I, page 140) and the VSAT-Radar Current network (being renewed per Annex IV), while minimizing the inherent jitter present in "Real Time" circuits (Annex I, page 141) and contributing to the stated goal of improving air operation safety (page 5). Since the specification does not state this requirement explicitly for all analog services, yet strongly implies it in the cited sections to guarantee interoperability and scalability (e.g., 30% bandwidth expansion capacity, page 25), we would like to confirm our interpretation to ensure a fully compliant offer. Clarification Question: Does CORPAC confirm that, for those analog services and interfaces (such as the VHF-ER and technical coordination channels detailed in Annex III) that require conversion to digital format for transport over the New VSAT-Radar Network, compliance with the EUROCAE ED-137/1C standard (or a superior equivalent) is mandatory, as inferred from sections 5.2.7 (b), 5.5.11(j), and 5.5.11(t)? This would allow us to propose a solution that maximizes compatibility with ICAO standards and RAP 310, directly contributing to the network's resilience over the next 10 years (General Objective, page 5)."

**RESPONSE:**

As described in Annex III, the analog interfaces for VHF-ER are E&M, and those used in telephony (ATS Call and Technical Coordination) are FXS.

The technical specifications document details that each bidder's solution must comply with the EUROCAE ED-137/1C standard (“Interoperability Standards for VoIP Components Volume 1: Radio”). Access equipment must be included in each supplier's proposal and meet the requirements of the technical specifications. The method of analog-to-digital signal conversion, multiplexing, and IP packaging is part of each bidder's technical solution.

The Iquitos node is considered the "Master Backup" node ONLY to maintain synchronization of the full-mesh VSAT network in the event of a failure of the "Master" node in Lima, using TDMA/MF-TDMA access technology in the modem. The Iquitos node will NOT receive applications from other nodes in the event of a failure of the Lima node.

3) Chapter 3.1.8.1 defines the qualification of the training personnel related to project management. In the training content (chapter 7.5) there are only technical elements that do not relate to project management skills.

Can you please confirm that all technical training (according to chapter 7.5ff) shall be done by one trainer who qualifies as satellite service specialist?

**RESPONSE:**

The requirements described for key personnel (paragraph 3.1.8.1) are related to the full implementation of the project, which will be the responsibility of the Project Manager, and to the provision of on-site technical support and support for the warranty period, which will be carried out by the Satellite Services Specialist.

It is incorrect to understand that the training foreseen in Section 7.5 (Factory Training, Local Training and On-the-Job Training) shall necessarily be the responsibility of the key personnel listed in the previous paragraph. Therefore, the training shall be taught by professionals of recognized experience in the equipment offered by the Contractor, capable of answering any doubts or questions on the hardware and software that may arise during the classes.

4) Chapter 7.5.3.5 defines that “Instructors must be specialists from the satellite equipment manufacturer”.

Can you please confirm that any instructor who qualifies according to chapter 3.1.8.1, regardless if he is employed by the satellite equipment manufacturer, complies as instructor for the trainings?

**RESPONSE:**

The training described in Section 7.5 shall be taught by professionals with recognized experience and in-depth knowledge of the equipment, and who are certified by the equipment manufacturer.

5) Chapter 5.5.9.1.1 point “d” defines that the equipment “must have the capacity to configure the G.728 (LD-CELP) CODEC”. In the ED-137/1C standard there is the possibility to utilize, beside G.711, either G.728 or G.729 voice codecs for compression.

Can you please confirm whether the support of G.711 and G.729 voice codec, in line with ED-137/1C, is sufficient to comply with the requirements.

**RESPONSE:**

It is confirmed.

6) Chapter 5.5.11.1 point “v” asks for compliance with H.323, which is clearly in contrast with ED-137 and is not utilized in this context.

Can you please confirm that the support of H.323 is not required due to the fact that it is not utilized and not defined in ED-137.

**RESPONSE:**

It is confirmed.

7) Chapter 6.3.2 defines that the software user interface must be in Spanish language. Concerning the scope requested in the tender, there are several technology components (VSAT, microwave, NMS, gateways) which are commercial off the shelf components manufactured by specialized companies. Typically, these products are globally utilized in the context of air traffic management and as such the common language supported and utilized in these regions is English. The requirement of a user interface in Spanish language would lead to a strong customization of these products to meet the requirements for this specific installation.

Can you please confirm if, given the above described circumstances, a user interface in English language would be acceptable?

**RESPONSE:**

It is acceptable. The updated version of the technical specifications will include this information as follows:

### *6.3.2 El etiquetado y señalización en los equipos proporcionados deberá ser español. Las interfaces de usuario para los equipos* ***commercial off the shelf*** *podrán estar en inglés o en español.*

8) Chapter 7.5.3.1 defines that “the Training Programme shall be carried out at the headquarters of the satellite equipment manufacturer”.

Can you please confirm that, in case the contractor is not the satellite equipment manufacturer, the training can also be carried out at the contractor’s facilities?

**RESPONSE:**

The training shall be conducted at the most appropriate place as determined by the contractor, insofar such place has the necessary test equipment and resources for the training to be conducted effectively.

The same applies for the related Factory Acceptance Test (FAT).

9) **Factory Training:**

a. Is it expected that we cover all expenses related to the Factory Training for the participants like for the FAT?

b. If yes, could you please confirm the exact scope (number of participants, duration, and included services)?

**RESPONSE:**

1. Please refer to the technical specifications, paragraph 8.2.12.
2. Please refer to the technical specifications, paragraph 7.5.3.1. and the subsequent paragraphs 7.5.3.2 through 7.5.3.9.

10) **Incoterms, Inland delivery:**

Would it be possible to adapt the Incoterms DDP so that the inland transportation after the site inspection is under your responsibility? E.g. we are responsible for the transport to the warehouse in Lima for inspection, and then you are responsible for the transport from Lima to the other seven sites.

**RESPONSE:**

This is not possible. The contractor will be responsible for the goods until the Certificate of Conformity is signed. The goods must be delivered in accordance with section 7.7 of the technical specifications.

11) **Warranty:**

Could you please provide a detailed definition of the 5-year warranty scope?

**RESPONSE:**

Paragraph 7.9.2 of the technical specifications defines the requirements for the technical warranty.

12) Chapter 10.2 defines the language of the proposal to be Spanish and Chapter 10.3 point “c” explains some exceptions.

Can you please confirm that it is admissible to provide the technical solution description in Spanish language, while additional documents such as data sheets, the product description and technical brochures are kept only in English language (without the need for a simple translation to Spanish).

**RESPONSE:**

This is not confirmed. The documentation must be submitted in accordance with what is indicated in section 10.3. Only complementary technical information contained in brochures, manuals, catalogs, or similar materials may be submitted in English without translation.

13) **Support after SAT:**

1-Year On-Site Technical Support after SAT: Could you please specify the exact scope? Does 24/7 support refer to remote support only, or is 24/7 on-site support also expected?

**RESPONSE:**

The one-year **technical support** will be on-site and will be provided in the VSAT Room in Lima, between 08:30 and 16:30, from Monday to Friday according to the Note included after paragraph 7.9.3.1 b). Outside these hours, telephone and/or email support channels must be established to provide support as specified in section 7.9.3 of the technical specifications.

If it is necessary to have a technician on-site to resolve an issue or failure, the company will send the appropriate technician to fix the problem as part of the **warranty** (paragraph 7.9.2).

A new note 2 has been included in the technical specifications to clarify this aspect as follows:

*“Nota 2: En caso de que exista alguna falla o inoperancia en algún sitio que requiera apoyo técnico presencial, la atención será realizada por un técnico de la empresa como parte de la garantía descrita en el párrafo 7.9.2.”*

14) The site surveys are scheduled to last until March 18th and are the technical baseline that serves as input for preparing a technical offer. Bid submission is April 20th.

Given the complexity of the scope and also the need of translating large quantities of documents into Spanish language to fulfil the tender requirements we would need more time between end of the site surveys and bid submission to ensure preparation of a high quality technical and financial proposal.

We would ask to extend the bid submission date by 6 weeks to June 1st, 2026.

**RESPONSE:**

The bid submission deadline for this tender has been extended until **Monday, 4 May 2025 by 16:00 hours Montreal time.**

15) We are working on the project ITB-ICAO-00068 / VSAT-RADAR NETWORK, with a bid submission deadline of April 20th, 2026.

The tender includes numerous systems and requires specific studies for each one. We are finishing the site visits, in order to provide you with a credible and functional offer, we request an extension of the deadline to June 16th, 2026.

**RESPONSE:**

Please see response to the previous question.

16) Les solicitamos amablemente, una extensión del plazo para la presentación de ofertas de la siguiente Descripción:

- «Nueva red de radares VSAT para la Corporación Peruana de Aeropuertos y Aviación Comercial (CORPAC)».

- REF: ICAO-00068

Tenemos un gran interés en participar en este proceso, por lo que le pedimos amablemente, que consideren una posible extensión de 30 días calendario adicionales a la fecha de entrega actual.

Con el fin de preparar nuestra licitación con la mayor profesionalidad, calidad y competitividad que caracterizan a nuestra empresa.

**RESPUESTA:**

Ver respuesta a la pregunta número 14.

17) With the postponement of the deadline for requests for clarification, are you considering a postponement of the deadline for submitting the overall offer? For example, following our request of March 17th, we asked for a postponement to June 2026.

**RESPONSE:**

Please see response to question 14.