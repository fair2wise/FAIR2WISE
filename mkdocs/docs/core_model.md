# Core data model

The LinkML schema is defined in `storage/schema/matkg_schema.yaml`. Extraction
records are richer working objects; `json2kg.py` transforms them into graph
nodes and associations; Splash Links then stores those records as generic SQL
entities and links.

## MatKG class model

```mermaid
classDiagram
    class Graph {
      Thing[] things
      Association[] associations
    }
    class Thing {
      string id
      string name
      string category
      string description
      Publication[] publications
    }
    class Material
    class ChemicalEntity
    class Device
    class Property
    class Method
    class ExperimentalTechnique
    class CodeSnippet {
      string function_name
      string code_language
      string code_snippet
      DomainFeature[] domain_features
    }
    class Publication {
      string source_paper
      int publication_year
      string paper_title
      string[] authors
      string doi
    }
    class Association {
      string subject
      string predicate
      string object
      string has_evidence
    }

    Graph "1" o-- "*" Thing
    Graph "1" o-- "*" Association
    Thing <|-- Material
    Thing <|-- ChemicalEntity
    Thing <|-- Device
    Thing <|-- Property
    Thing <|-- Method
    Thing <|-- ExperimentalTechnique
    Thing <|-- CodeSnippet
    Thing "1" o-- "*" Publication
    Association --> Thing : subject
    Association --> Thing : object
```

The actual schema includes additional general classes (`Component`,
`Condition`, `Interface`, `Measurement`, `Parameter`, `Phenomenon`, `Process`,
and `Structure`) and specialized material/device/property types.

## Extraction model

`TermRecord` is the cumulative pre-graph model:

```text
TermRecord
├── term, definition, category, raw_category
├── formula and formula_validation
├── RelationRecord[]
├── PropertyRecord[]
├── pages[] and source_papers[]
├── ContextSnippet[]
├── source_metadata{source_paper -> publication fields}
└── publications[]
```

Top-level extraction JSON additionally carries `metadata` and
`code_snippets`.

## MatKG to Splash mapping

| MatKG | Splash Links |
|---|---|
| `thing.id` | entity `uri`, plus `properties.matkg_id` |
| `thing.name` | entity `name` |
| `thing.category` | entity `entity_type` |
| Other node fields | entity `properties` |
| Association subject/object IDs | link subject/object UUIDs |
| Association predicate | link `predicate` |
| Evidence/source file | link `properties` |

This mapping lets Splash use internal UUIDs while the application preserves
stable MatKG identifiers.

## UI normalization

The agent API maps graph records to:

```text
GraphPayload
├── nodes: {id, label, type, description, publications, code, linked snippets}
├── edges: {source, target, predicate}
└── source_path
```

Publication fields are normalized and deduplicated before reaching the UI.
Code snippets may be first-class nodes and also appear as linked detail records.
