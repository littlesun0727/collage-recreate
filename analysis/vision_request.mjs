/** Host-independent stage adapter; internal requests use the audited yibu route. */
import {main} from '../model_api/qwen_request.mjs';
import {outputContract} from '../structured_output.mjs';
await main({task:'analysis',contract:outputContract,reasoning:'high'});
