-- O nome do imóvel só precisa ser único dentro do mesmo cliente: dois clientes podem ter um "Sítio São José".
-- NULLS NOT DISTINCT: serviços sem cliente continuam sem repetir nome e revisão entre si.

ALTER TABLE servicos DROP CONSTRAINT IF EXISTS servicos_empresa_id_nome_imovel_revisao_key;
ALTER TABLE servicos ADD CONSTRAINT servicos_empresa_cliente_nome_revisao_key
  UNIQUE NULLS NOT DISTINCT (empresa_id, cliente_id, nome_imovel, revisao);
