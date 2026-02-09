import React, { useState, useRef, useEffect } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';
import { Send, Check, X, Database, Terminal, Loader, Table as TableIcon, Square } from 'lucide-react';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

/* --- Helper Components --- */

const TableComponent = ({ data }) => {
    if (!data || !data.headers || !data.rows) return null;
    return (
        <div className="table-wrapper">
            <table>
                <thead>
                    <tr>
                        {data.headers.map((h, i) => <th key={i}>{h}</th>)}
                    </tr>
                </thead>
                <tbody>
                    {data.rows.map((row, i) => (
                        <tr key={i}>
                            {row.map((cell, j) => <td key={j}>{cell}</td>)}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
};

function App() {
    // History stores all interactions. Types: 'user' | 'assistant' | 'proposal' | 'result' | 'error' | 'system'
    // Proposal msg structure: { type: 'proposal', id: string, query: string, status: 'pending'|'approved'|'rejected', full_responses: string }
    // Result msg structure: { type: 'result', data: {headers:[], rows:[]} }
    const [history, setHistory] = useState([
        { type: 'assistant', content: 'Hello! I am your SQL Assistant. Ask me anything about your business data.' }
    ]);

    const [input, setInput] = useState('');
    const [loading, setLoading] = useState(false);
    const [threadId, setThreadId] = useState(null);
    const [activeProposalId, setActiveProposalId] = useState(null); // Track which proposal is waiting for action
    const [autoExecute, setAutoExecute] = useState(false); // Toggle state

    const messagesEndRef = useRef(null);
    const abortControllerRef = useRef(null);

    const scrollToBottom = () => {
        setTimeout(() => {
            messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
        }, 100);
    };

    useEffect(scrollToBottom, [history]);

    const handleStop = () => {
        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
            abortControllerRef.current = null;
        }
        setLoading(false);
        // Optionally add a message indicating stoppage
        // setHistory(prev => [...prev.filter(m => m.type !== 'assistant' || m.content !== '...'), { type: 'info', content: 'Generation stopped by user.' }]);
    };

    const handleSubmit = async () => {
        if (!input.trim() || activeProposalId) return;

        // specific check: if loading, do NOT submit, let the stop button handle it (UI prevents this usually)
        if (loading) return;

        const userMsg = input.trim();
        setInput('');
        setLoading(true);

        setHistory(prev => [...prev, { type: 'user', content: userMsg }]);

        // Create new AbortController
        abortControllerRef.current = new AbortController();

        try {
            const response = await axios.post(`${API_URL}/chat`, {
                message: userMsg,
                thread_id: threadId,
                auto_execute: autoExecute
            }, {
                signal: abortControllerRef.current.signal
            });

            const data = response.data;
            setThreadId(data.thread_id);

            if (data.status === 'approval_required') {
                // Parse Query for display
                const sqlMatch = data.response.match(/```sql([\s\S]*?)```/);
                const sqlQuery = sqlMatch ? sqlMatch[1].trim() : data.response;

                // Create a Proposal Message
                const newProposal = {
                    type: 'proposal',
                    id: Date.now(), // simple ID
                    query: sqlQuery,
                    full_text: data.response,
                    status: 'pending'
                };

                setHistory(prev => [...prev, newProposal]);
                setActiveProposalId(newProposal.id);
            } else if (data.structured_data) {
                // Auto-execution result with structured data

                // If query is returned, show it first
                if (data.query) {
                    setHistory(prev => [...prev, {
                        type: 'proposal',
                        id: Date.now(),
                        query: data.query,
                        status: 'approved' // Mark as already approved
                    }]);
                } else {
                    setHistory(prev => [...prev, { type: 'assistant', content: "Query executed automatically." }]);
                }

                setHistory(prev => [...prev, {
                    type: 'result',
                    data: data.structured_data
                }]);
            } else {
                setHistory(prev => [...prev, { type: 'assistant', content: data.response }]);
            }
        } catch (error) {
            if (axios.isCancel(error)) {
                console.log('Request canceled', error.message);
            } else {
                console.error(error);
                setHistory(prev => [...prev, { type: 'error', content: 'Connection Error.' }]);
            }
        } finally {
            setLoading(false);
            abortControllerRef.current = null;
        }
    };

    const handleApproval = async (decision) => {
        if (!activeProposalId) return;

        setLoading(true);

        // Update local state to show action taken immediately
        setHistory(prev => prev.map(msg => {
            if (msg.type === 'proposal' && msg.id === activeProposalId) {
                return { ...msg, status: decision === 'approve' ? 'approved' : 'rejected' };
            }
            return msg;
        }));

        // Add system message
        if (decision === 'approve') {
            setHistory(prev => [...prev, { type: 'system', content: 'Executing query...' }]);
        } else {
            setHistory(prev => [...prev, { type: 'info', content: 'Query execution rejected.' }]);

            // INSTANT UX: Unlock immediately for rejection
            setActiveProposalId(null);
            setLoading(false);
            setTimeout(() => {
                const inputEl = document.querySelector('.input-box');
                if (inputEl) inputEl.focus();
            }, 50);

            // Send to backend in background (don't await for UI unlock)
            axios.post(`${API_URL}/approval`, {
                decision: decision,
                thread_id: threadId
            }).catch(err => console.error("Background rejection sync failed", err));

            return; // Exit early, user is ready to type
        }

        try {
            const response = await axios.post(`${API_URL}/approval`, {
                decision: decision,
                thread_id: threadId
            });

            const data = response.data;

            if (data.structured_data) {
                // Rich Result
                setHistory(prev => [...prev, {
                    type: 'result',
                    data: data.structured_data
                }]);
            } else {
                // Text fallback
                setHistory(prev => [...prev, { type: 'assistant', content: data.response }]);
            }

        } catch (error) {
            setHistory(prev => [...prev, { type: 'error', content: 'Approval Error.' }]);
        } finally {
            setActiveProposalId(null);
            setLoading(false);

            // Auto-focus input on rejection (or completion)
            if (decision === 'reject') {
                setTimeout(() => {
                    const inputEl = document.querySelector('.input-box');
                    if (inputEl) inputEl.focus();
                }, 50);
            }
        }
    };

    return (
        <div className="app-container">
            <div className="header">
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <Database size={24} color="#06b6d4" />
                    <h1>Agentic SQL Assistant</h1>
                </div>

                <div className="mode-toggle">
                    <label className={`toggle-option ${!autoExecute ? 'active' : ''}`}>
                        <input
                            type="radio"
                            name="mode"
                            checked={!autoExecute}
                            onChange={() => setAutoExecute(false)}
                        />
                        Human in Loop
                    </label>
                    <label className={`toggle-option ${autoExecute ? 'active' : ''}`}>
                        <input
                            type="radio"
                            name="mode"
                            checked={autoExecute}
                            onChange={() => setAutoExecute(true)}
                        />
                        Automatic
                    </label>
                </div>
            </div>

            <div className="main-content">
                <div className="message-list">
                    {history.map((msg, idx) => {
                        // Renders based on type
                        if (msg.type === 'proposal') {
                            return (
                                <div key={idx} className="query-card persistent">
                                    <h3><Terminal size={20} /> Operation Proposal</h3>
                                    <div className="sql-block">{msg.query}</div>

                                    {msg.status === 'pending' && (
                                        <div className="approval-actions">
                                            <button
                                                className="btn btn-confirm"
                                                onClick={() => handleApproval('approve')}
                                                disabled={loading}
                                            >
                                                <Check size={18} /> Confirm & Execute
                                            </button>
                                            <button
                                                className="btn btn-reject"
                                                onClick={() => handleApproval('reject')}
                                                disabled={loading}
                                            >
                                                <X size={18} /> Reject
                                            </button>
                                        </div>
                                    )}
                                    {msg.status === 'approved' && (
                                        <div className="status-badge success"><Check size={14} /> Approved for Execution</div>
                                    )}
                                    {msg.status === 'rejected' && (
                                        <div className="status-badge error"><X size={14} /> Rejected by User</div>
                                    )}
                                </div>
                            );
                        }

                        if (msg.type === 'result') {
                            return (
                                <div key={idx} className="result-section">
                                    <h3><TableIcon size={20} /> Query Results</h3>
                                    <TableComponent data={msg.data} />
                                </div>
                            );
                        }

                        // Standard messages
                        return (
                            <div key={idx} className={`message ${msg.type}`}>
                                <ReactMarkdown>{msg.content}</ReactMarkdown>
                            </div>
                        );
                    })}

                    {loading && (
                        <div className="message assistant">
                            <span className="loading-dots"><Loader className="animate-spin" size={16} /></span>
                        </div>
                    )}
                </div>
                <div ref={messagesEndRef} />
            </div>

            <div className="input-area">
                <div className="input-container">
                    <input
                        className="input-box"
                        placeholder="Describe the data you need..."
                        value={input}
                        onChange={e => setInput(e.target.value)}
                        onKeyDown={e => e.key === 'Enter' && !loading && handleSubmit()}
                        disabled={!!activeProposalId}
                    />
                    {loading ? (
                        <button
                            className="send-btn stop-btn"
                            onClick={handleStop}
                            title="Stop Generating"
                            style={{ backgroundColor: '#ef4444' }} // Red for Stop
                        >
                            <Square size={20} fill="white" />
                        </button>
                    ) : (
                        <button
                            className="send-btn"
                            onClick={handleSubmit}
                            disabled={!input.trim() || !!activeProposalId}
                        >
                            <Send size={20} />
                        </button>
                    )}
                </div>
            </div>
        </div>
    )
}

export default App
