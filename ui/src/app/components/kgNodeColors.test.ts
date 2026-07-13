import { describe, expect, it } from 'vitest';
import {
  getNodeColor,
  isSchemaNodeCategory,
  isUnknownNodeCategory,
  LLM_INVENTED_NODE_COLOR,
  UNKNOWN_NODE_COLOR,
} from './kgNodeColors';

describe('kgNodeColors', () => {
  it('colors schema classes with rainbow palette', () => {
    expect(getNodeColor('Material')).not.toBe(LLM_INVENTED_NODE_COLOR);
    expect(getNodeColor('Material')).not.toBe(UNKNOWN_NODE_COLOR);
    expect(getNodeColor('ExperimentalTechnique')).not.toBe(LLM_INVENTED_NODE_COLOR);
  });

  it('uses sky blue for LLM-invented categories', () => {
    expect(getNodeColor('ImagingTechnique')).toBe(LLM_INVENTED_NODE_COLOR);
    expect(isSchemaNodeCategory('ImagingTechnique')).toBe(false);
  });

  it('uses gray only for Unknown nodes', () => {
    expect(getNodeColor('Unknown')).toBe(UNKNOWN_NODE_COLOR);
    expect(getNodeColor('unknown')).toBe(UNKNOWN_NODE_COLOR);
  });

  it('identifies Unknown stub categories for UI filtering', () => {
    expect(isUnknownNodeCategory('Unknown')).toBe(true);
    expect(isUnknownNodeCategory('matkg:Unknown')).toBe(true);
    expect(isUnknownNodeCategory('')).toBe(true);
    expect(isUnknownNodeCategory('Material')).toBe(false);
  });
});
