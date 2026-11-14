# Declaração de uso de inteligência artificial

Este documento declara como sistemas de IA generativa participaram da produção deste repositório, em
conformidade com as diretrizes de integridade científica do **Conselho Nacional de Desenvolvimento
Científico e Tecnológico (Portaria CNPq nº 2.664/2026)** e com a posição do **Committee on Publication
Ethics (COPE)** sobre autoria por agentes artificiais.

## O que a IA fez

| atividade | escopo |
|---|---|
| **Redação de código** | `scripts/` (coletor, contratos pydantic, validador) e `tests/` foram escritos em diálogo com um assistente, a partir do desenho do pesquisador |
| **Documentação** | este arquivo, o `README.md` e os docstrings das funções |
| **Revisão crítica** | leitura do código à procura de comentário inútil, caminho pessoal vazando, lógica redundante e variável de ambiente duplicada |

## O que a IA não fez

- **Nenhum resultado empírico.** Toda resposta registrada em `data/` é saída literal de uma chamada HTTP a
  um modelo de terceiros, preservada como veio, inclusive quando está errada. A IA não escreve, corrige nem
  filtra resposta nenhuma.
- **Nenhuma conclusão.** O repositório não interpreta o que coleta: não atribui nota, não classifica acerto e
  não declara mudança de comportamento. Essa separação é a regra de arquitetura do projeto, não uma escolha
  editorial.
- **Nenhum gabarito por conta própria.** Os itens do bloco F têm valor esperado e fonte primária conferidos
  pelo pesquisador na data indicada em `questions.jsonl` (decreto, lei, texto constitucional). A IA ajudou a
  redigir a pergunta; não decidiu o que é verdade.

## Como verificar

1. **O artefato é auditável por construção.** Cada linha de `data/` traz `model_id`, `route`, `declared_version`,
   `params`, `latency_ms`, `tokens` e `cost_usd`. Qualquer pessoa pode refazer a chamada e comparar.
2. **A bateria é aberta e versionada.** `questions.jsonl` está no repositório com versão (`v1.0.0`) e data de
   ativação por item. Todo registro carrega a versão com que foi coletado.
3. **O código é testado e legível.** `pytest` e `ruff` rodam no CI a cada push; o código não tem comentário de
   IA nem atalho não documentado.
4. **O histórico do repositório é público.** Os commits mostram a evolução real do instrumento, incluindo as
   correções — não uma versão idealizada escrita de uma vez.

## Responsabilidade

O pesquisador humano mantém responsabilidade científica e ética integral e não delegável pelo desenho do
instrumento, pela escolha da bateria, pela coleta, pela integridade do artefato e por qualquer afirmação
derivada dele. Nenhum sistema de IA é autor ou coautor deste repositório.

## Referências normativas

- **COPE** — *Authorship and AI tools*, Committee on Publication Ethics, 2023.
- **CNPq** — *Diretrizes de Integridade Científica*, Portaria nº 2.664/2026.
- **STM Association** — *Global Reporting Standard for AI Disclosure in Research*.
