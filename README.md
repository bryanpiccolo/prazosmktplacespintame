# Monitor de prazos de entrega — Pinta Me

**Painel:** https://bryanpiccolo.github.io/prazosmktplacespintame/

Todo dia às 6h05, este repositório consulta sozinho, no GitHub Actions, o prazo de entrega que o Mercado Livre promete para 54 destinos: uma capital e uma cidade do interior de cada estado. Depois ele salva o histórico em `dados/historico.sqlite` e atualiza o painel acima.

O painel é público e mostra **só prazos**, sem nenhum dado de vendas.

## E-mails
Os e-mails não saem daqui. Eles são enviados por agendamentos no Claude, pelo Gmail, para comercial@pintame.com.br e vimuller5@gmail.com:
- **Alerta (todo dia de manhã):** quando um prazo fica pelo menos 2 dias ou 30% acima do normal daquele destino, quando o frete deixa de ser grátis, quando some a opção de entrega ou quando a coleta falha.
- **Resumo (segunda, 7h30):** a semana anterior, o mês até agora e a média dos meses fechados.

## Ajustes
- **Anúncios monitorados:** ficam em `config.json → ml.anuncios`. Hoje é o vestido Passarinho (MLB5146553103). Como o prazo depende da forma de envio e não do produto, um anúncio por forma de envio basta.
- **Destinos:** ficam em `ceps.csv`. Se o ML desativar algum CEP, basta trocar.
- **Sensibilidade dos alertas:** fica em `config.json → alertas`.
- **Rodar a coleta na hora:** Actions → **Coleta diária de prazos** → **Run workflow**.

## Como é calculado
- O prazo considerado é o da opção de frete que o Mercado Livre mostra por padrão ao comprador, contado em dias corridos a partir do dia da consulta.
- A consulta usa o cálculo público de frete do ML, sem login.
- **Shopee:** a coleta está desligada, porque ela só informa o prazo no checkout (verificado em 25/09/2026). O caminho para trazer a Shopee é a API de vendedor (Shopee Open Platform).
